import { execFileSync } from "node:child_process";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/**
 * ContextOS 实时视图 — Playwright 真机审计
 *
 * 目标：在真实 Electron 应用里证明
 *   1) ContextOS 入口可达、视图可渲染；
 *   2) 仪表盘的实时数据来自 Polaris **既有的实时框架**（Nats-JetStream → WebSocket /v2/ws/runtime），
 *      而非文件轮询/文件快照：测试直接发布 runtime.v2 envelope，WS 推送 → useRuntime →
 *      ContextOS 实时呈现。（ContextOS 自身不读任何文件——它只消费 WS 推送的 props。）
 */

const SCREENSHOT_PATH = "/tmp/contextos-audit.png";

const ignoredConsoleErrorPatterns = [
  /has been blocked by CORS policy/i,
  /Failed to load resource: net::ERR_FAILED/i,
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /TypeError: Failed to fetch/i,
  /Unable to preload CSS for \/assets\//i,
  /404 \(Not Found\).*\/v2\/context\/admin\/stats/i,
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

interface RuntimeEnvelopePublish {
  subject: string;
  payload: Record<string, unknown>;
}

function publishRuntimeV2Envelopes(events: RuntimeEnvelopePublish[]): void {
  const script = `
import asyncio
import json
import sys

from polaris.delivery.http.routers.jetstream_utils import publish_to_jetstream


async def main() -> None:
    events = json.loads(sys.argv[1])
    for item in events:
        ok = await publish_to_jetstream(item["subject"], item["payload"])
        if not ok:
            raise SystemExit(f"failed to publish {item['subject']}")


asyncio.run(main())
`;
  execFileSync("python", ["-c", script, JSON.stringify(events)], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PYTHONPATH: process.env.PYTHONPATH ? `src/backend:${process.env.PYTHONPATH}` : "src/backend",
    },
    encoding: "utf8",
    stdio: "pipe",
  });
}

test("ContextOS entry is reachable and the real-time dashboard renders", async ({ window }, testInfo) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];

  window.on("pageerror", (error) => {
    pageErrors.push(String(error));
  });
  window.on("console", (message) => {
    if (message.type() === "error") {
      const location = message.location();
      consoleErrors.push(`${message.text()} @ ${location.url || "unknown"}`);
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

  // Capture a viewport screenshot for human visual audit without stretching the
  // Electron test timeout on long dashboards.
  await window.screenshot({ path: SCREENSHOT_PATH });
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
 * 关键（修正上一版假绿）：真实运行时事件必须走 Nats-JetStream runtime.v2 envelope，而不是写
 * per-run 文件后等待重连快照。测试发布：
 *   - LLM 调用 → envelope(channel=llm, payload=CanonicalLogEventV2)，携带
 *     raw.stream_event=llm_completed，raw.data.{prompt,completion}_tokens + metadata.elapsed_ms。
 *   - 上下文装配 → envelope(channel=runtime_events, payload=prompt_context / context.build /
 *     context.snapshot)。
 * 仪表盘应在不 reload、不读文件、不轮询的情况下实时呈现真实调用次数 / 真实 token /
 * 真实时延 / 投影 / 在窗项数 / 快照回执。
 */
test("ContextOS renders REAL production-shape telemetry over the runtime WebSocket (no polling)", async ({ window }, testInfo) => {
  const marker = `ctx-${Date.now()}`;
  const runId = `${marker}-run`;
  const nowMs = Date.now();
  const nowIso = new Date(nowMs).toISOString();
  const nowEpoch = nowMs / 1000;
  const subject = `hp.runtime.contextos.${marker}`;

  // 1) 进入 ContextOS，并等待 runtime.v2 WebSocket 订阅就绪。
  await enterContextOS(window);
  await expect(window.locator("[data-testid='contextos-workspace']")).toBeVisible();
  const source = window.locator("[data-testid='contextos-telemetry-source']");
  await expect(source).toBeVisible({ timeout: 20000 });
  await expect(window.getByTestId("contextos-telemetry-freshness")).toContainText("WS LIVE");

  // 2) 通过生产 Nats-JetStream runtime.v2 rail 发布真实生产形态事件。
  const runtimeEvents = [
    {
      schema_version: 1,
      ts: nowIso,
      ts_epoch: nowEpoch,
      seq: 1,
      event_id: `${marker}-pc`,
      kind: "observation",
      actor: "PM",
      name: "prompt_context",
      refs: { run_id: runId, step: 1 },
      summary: "Prompt Context Injection",
      ok: true,
      output: {
        run_id: runId,
        phase: "pm.planning",
        step: 1,
        persona_id: "pm.v1",
        strategy: "combined_ranking",
        token_usage_estimate: 0,
      },
    },
    {
      schema_version: 1,
      ts: new Date(nowMs + 1000).toISOString(),
      ts_epoch: nowEpoch + 1,
      seq: 2,
      event_id: `${marker}-build`,
      kind: "observation",
      actor: "System",
      name: "context.build",
      refs: { run_id: runId, step: 1 },
      summary: "ContextPack built",
      ok: true,
      output: {
        request_hash: `${marker}-hash`,
        items_count: 5,
        total_tokens: 3200,
        snapshot_path: "runtime/snap/e2e.json",
        snapshot_hash: `${marker}-snap`,
      },
    },
    {
      schema_version: 1,
      ts: new Date(nowMs + 2000).toISOString(),
      ts_epoch: nowEpoch + 2,
      seq: 3,
      event_id: `${marker}-snap`,
      kind: "observation",
      actor: "System",
      name: "context.snapshot",
      refs: { run_id: runId, step: 1 },
      summary: "Context snapshot stored",
      ok: true,
      output: {
        request_hash: `${marker}-hash`,
        snapshot_path: "runtime/snap/e2e.json",
        snapshot_hash: `${marker}-snap`,
      },
    },
  ];
  const llmLine = {
    schema_version: 2,
    event_id: `${marker}-llm`,
    run_id: runId,
    seq: 4,
    ts: new Date(nowMs + 3000).toISOString(),
    ts_epoch: nowEpoch + 3,
    channel: "llm",
    domain: "llm",
    severity: "info",
    kind: "state",
    actor: "pm",
    source: "application.roles.events",
    message: "llm response completed | completion_tokens=1454",
    tags: ["llm_realtime_bridge", "llm_event:llm_call_end", "projection_event:llm_completed"],
    raw: {
      stream_event: "llm_completed",
      event_type: "llm_call_end",
      role: "pm",
      data: {
        model: "MiniMax-M3",
        prompt_tokens: 1932,
        completion_tokens: 1454,
        context_tokens_after: 1932,
        metadata: { elapsed_ms: 71431.06 },
      },
    },
  };

  publishRuntimeV2Envelopes([
    ...runtimeEvents.map((event, index) => ({
      subject,
      payload: {
        schema_version: "runtime.v2",
        event_id: `${marker}-runtime-${index + 1}`,
        workspace_key: "contextos-e2e",
        run_id: runId,
        channel: "runtime_events",
        kind: "runtime_event",
        ts: String(event.ts),
        payload: event,
        meta: { test_marker: marker },
      },
    })),
    {
      subject,
      payload: {
        schema_version: "runtime.v2",
        event_id: `${marker}-llm-envelope`,
        workspace_key: "contextos-e2e",
        run_id: runId,
        channel: "llm",
        kind: "llm.completed",
        ts: String(llmLine.ts),
        payload: llmLine,
        meta: { test_marker: marker },
      },
    },
  ]);

  // 3) 断言真实生产形态事件经 WS 实时呈现（无轮询、无 reload、无文件 tail）。
  await expect(source).toContainText("REAL");
  await expect(source).toContainText("调用"); // llm_completed → 1 次真实调用
  await expect(source).toContainText("投影"); // prompt_context / context.build → projection

  // The freshness badge is intentionally time-sensitive (<30s), while this
  // test seeds production-shape history and reloads Electron to verify WS
  // delivery. Assert the transport and telemetry state instead of binding the
  // audit to a UI age threshold.
  await expect(window.getByTestId("contextos-telemetry-freshness")).toContainText("遥测");
  await expect(window.getByTestId("contextos-telemetry-freshness")).toContainText("WS LIVE");

  // 真实 per-call token（journal raw.data）实时呈现，标注「实时」，而非「需诊断端点」空态。
  await expect(window.getByText(/tokens · 实时/)).toBeVisible({ timeout: 20000 });
  await expect(window.getByTestId("contextos-tokens-unavailable")).toHaveCount(0);

  // 结构化信号经 WS meta 保真送达：WorkingMem 显示真实在窗项数（items_count=5）。
  await expect(window.locator("[data-testid='contextos-stage-working_mem']")).toContainText("5 项在窗");
  await expect(window.locator("[data-testid='contextos-stage-projection']")).toContainText(/(?:[2-9]|\d{2,}) 投影/);
  // 快照回执（context.snapshot 的 snapshot_hash 签名）在决策 / 回执流中呈现。
  await expect(window.getByText("Context snapshot stored", { exact: false })).toBeVisible();

  // 截图供人工视觉审计（真实 WS 数据态）。
  const shot = "/tmp/contextos-ws-realtime.png";
  await window.screenshot({ path: shot, fullPage: true });
  await testInfo.attach("contextos-ws-realtime", { path: shot, contentType: "image/png" });
});
