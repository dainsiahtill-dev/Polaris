import type { Page, Request, Response, TestInfo, WebSocket } from "@playwright/test";
import { expect, test } from "./fixtures";

type BackendInfo = {
  baseUrl?: string;
  token?: string;
};

type BenchPostResult = {
  session_id?: string;
  appended?: boolean;
  published?: boolean;
};

type WebSocketFrameCapture = {
  direction: "sent" | "received";
  payload: string;
  timestamp: string;
};

type RealtimeNetworkRequest = {
  method: string;
  url: string;
  resourceType: string;
  accept: string;
  timestamp: string;
};

type RealtimeNetworkResponse = {
  status: number;
  url: string;
  contentType: string;
  timestamp: string;
};

type RealtimeNetworkAudit = {
  requests: RealtimeNetworkRequest[];
  responses: RealtimeNetworkResponse[];
  webSockets: string[];
  detach: () => void;
};

function startRealtimeNetworkAudit(window: Page): RealtimeNetworkAudit {
  const requests: RealtimeNetworkRequest[] = [];
  const responses: RealtimeNetworkResponse[] = [];
  const webSockets: string[] = [];

  const onRequest = (req: Request) => {
    requests.push({
      method: req.method(),
      url: req.url(),
      resourceType: req.resourceType(),
      accept: req.headers().accept || "",
      timestamp: new Date().toISOString(),
    });
  };
  const onResponse = (res: Response) => {
    responses.push({
      status: res.status(),
      url: res.url(),
      contentType: res.headers()["content-type"] || "",
      timestamp: new Date().toISOString(),
    });
  };
  const onWebSocket = (webSocket: WebSocket) => {
    webSockets.push(webSocket.url());
  };

  window.on("request", onRequest);
  window.on("response", onResponse);
  window.on("websocket", onWebSocket);

  return {
    requests,
    responses,
    webSockets,
    detach: () => {
      window.off("request", onRequest);
      window.off("response", onResponse);
      window.off("websocket", onWebSocket);
    },
  };
}

function isForbiddenRealtimeRequest(request: RealtimeNetworkRequest): boolean {
  const url = new URL(request.url);
  const path = url.pathname;
  const accept = request.accept.toLowerCase();
  return (
    request.resourceType === "eventsource" ||
    accept.includes("text/event-stream") ||
    path.includes("/stream") ||
    path === "/files/read"
  );
}

function monitoredPollingKey(request: RealtimeNetworkRequest): string | null {
  if (request.method !== "GET") return null;
  const url = new URL(request.url);
  const path = url.pathname;
  if (path === "/v2/state/snapshot") return path;
  if (path === "/v2/pm/status") return path;
  if (path === "/v2/director/status") return path;
  if (path === "/v2/llm/status") return path;
  return null;
}

async function attachRealtimeNetworkAudit(
  testInfo: TestInfo,
  audit: RealtimeNetworkAudit,
): Promise<void> {
  await testInfo.attach("realtime-network-audit.json", {
    body: JSON.stringify(
      {
        requests: audit.requests,
        responses: audit.responses,
        webSockets: audit.webSockets,
      },
      null,
      2,
    ),
    contentType: "application/json",
  });
}

function assertRealtimeNetworkAudit(
  audit: RealtimeNetworkAudit,
  frames: WebSocketFrameCapture[],
): void {
  const forbiddenRequests = audit.requests.filter(isForbiddenRealtimeRequest);
  const sseResponses = audit.responses.filter((response) =>
    response.contentType.toLowerCase().includes("text/event-stream"),
  );
  const monitoredCounts = new Map<string, RealtimeNetworkRequest[]>();
  for (const request of audit.requests) {
    const key = monitoredPollingKey(request);
    if (!key) continue;
    monitoredCounts.set(key, [...(monitoredCounts.get(key) || []), request]);
  }
  const repeatedPolling = Array.from(monitoredCounts.entries())
    .filter(([, entries]) => entries.length > 1)
    .map(([key, entries]) => ({
      key,
      count: entries.length,
      urls: entries.map((entry) => entry.url),
      timestamps: entries.map((entry) => entry.timestamp),
    }));

  const hasRuntimeV2WebSocket =
    audit.webSockets.some((url) => url.includes("/v2/ws/runtime")) ||
    frames.some((frame) => frame.payload.includes("runtime.v2") && frame.payload.includes("event.bench"));
  expect(hasRuntimeV2WebSocket, "runtime.v2 WebSocket should be used").toBe(true);
  expect(forbiddenRequests, "SSE, legacy stream, and file-read realtime requests must not occur").toEqual([]);
  expect(sseResponses, "HTTP responses must not use text/event-stream").toEqual([]);
  expect(repeatedPolling, "status/snapshot endpoints must not be repeatedly fetched as polling").toEqual([]);
}

async function startWebSocketFrameCapture(window: Page): Promise<{
  frames: WebSocketFrameCapture[];
  detach: () => Promise<void>;
}> {
  const frames: WebSocketFrameCapture[] = [];
  try {
    const session = await window.context().newCDPSession(window);
    await session.send("Network.enable");
    session.on("Network.webSocketFrameSent", (event) => {
      frames.push({
        direction: "sent",
        payload: String(event.response?.payloadData ?? ""),
        timestamp: new Date().toISOString(),
      });
    });
    session.on("Network.webSocketFrameReceived", (event) => {
      frames.push({
        direction: "received",
        payload: String(event.response?.payloadData ?? ""),
        timestamp: new Date().toISOString(),
      });
    });
    return { frames, detach: () => session.detach() };
  } catch {
    return { frames, detach: async () => undefined };
  }
}

async function attachWebSocketFrames(
  testInfo: TestInfo,
  label: string,
  frames: WebSocketFrameCapture[],
): Promise<void> {
  await testInfo.attach(`renderer-websocket-frames-${label}`, {
    body: frames.map((frame) => JSON.stringify(frame)).join("\n"),
    contentType: "application/jsonlines",
  });
}

async function waitForWebSocketPayload(
  frames: WebSocketFrameCapture[],
  predicate: (payload: string) => boolean,
  label: string,
  timeoutMs = 10_000,
): Promise<void> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (frames.some((frame) => predicate(frame.payload))) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Timed out waiting for WebSocket frame: ${label}`);
}

async function getBackendInfo(window: Page): Promise<Required<BackendInfo>> {
  const backend = await window.evaluate(async () => {
    const api = (window as unknown as { polaris?: { getBackendInfo?: () => Promise<BackendInfo> } }).polaris;
    if (!api?.getBackendInfo) {
      throw new Error("polaris.getBackendInfo missing");
    }
    return await api.getBackendInfo();
  });
  if (!backend.baseUrl || !backend.token) {
    throw new Error(`backend info incomplete: ${JSON.stringify(backend)}`);
  }
  return { baseUrl: backend.baseUrl, token: backend.token };
}

async function backendJson<T>(
  window: Page,
  endpoint: string,
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  const backend = await getBackendInfo(window);
  return await window.evaluate(
    async ({ baseUrl, token, apiPath, method, body }) => {
      const response = await fetch(`${baseUrl}${apiPath}`, {
        method,
        cache: "no-store",
        headers: {
          authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          Pragma: "no-cache",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(`${method} ${apiPath} failed: ${response.status} ${await response.text()}`);
      }
      return (await response.json()) as unknown;
    },
    {
      baseUrl: backend.baseUrl,
      token: backend.token,
      apiPath: endpoint,
      method: init.method || "GET",
      body: init.body,
    },
  ) as T;
}

async function enterPmWorkspace(window: Page): Promise<void> {
  const directEntry = window.locator("[data-testid='enter-pm-workspace']");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }
  await window.getByRole("button", { name: /更多功能/ }).click();
  await window.getByTestId("enter-pm-workspace").click();
}

async function enterChiefEngineerWorkspace(window: Page): Promise<void> {
  const directEntry = window.locator("[data-testid='enter-chief-engineer-workspace']");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }
  await window.getByRole("button", { name: /更多功能/ }).click();
  await window.getByTestId("enter-chief-engineer-workspace").click();
}

async function enterDirectorWorkspace(window: Page): Promise<void> {
  const directEntry = window.locator("[data-testid='enter-director-workspace']");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }
  await window.getByRole("button", { name: /更多功能/ }).click();
  await window.getByTestId("enter-director-workspace").click();
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

async function backToMain(window: Page, backTestId: string): Promise<void> {
  await window.getByTestId(backTestId).click();
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();
}

async function publishBenchMarker(
  window: Page,
  sessionId: string,
  surface: string,
  index: number,
): Promise<string> {
  const marker = `nats-jetstream-${surface}-${Date.now()}`;
  const result = await backendJson<BenchPostResult>(window, `/v2/factory/bench/sessions/${sessionId}/events`, {
    method: "POST",
    body: {
      type: "factory_bench.project.started",
      actor: "playwright",
      summary: marker,
      ok: true,
      meta: {
        session_id: sessionId,
        work_dir: `/tmp/${sessionId}`,
        project_id: `L${index}-01`,
        project_ids: ["L1-01", "L2-01", "L3-01", "L4-01", "L5-01", "L6-01", "L7-01", "L8-01"],
        total: 8,
        completed: Math.max(0, index - 1),
        failed: 0,
        status: "running",
        updated_at: new Date().toISOString(),
        metadata: {
          source: "playwright_realtime_nat_jetstream_workspaces",
          surface,
        },
      },
    },
  });
  expect(result.appended, `${surface} bench event should be appended`).toBe(true);
  expect(result.published, `${surface} bench event should publish to JetStream`).toBe(true);
  return marker;
}

async function expectBenchRealtimeMarker(
  window: Page,
  sessionId: string,
  marker: string,
  label: string,
): Promise<void> {
  const strip = window.locator(`[data-testid='bench-status-strip'][data-bench-session='${sessionId}']`).first();
  await expect(strip, `${label} bench strip should show the active session`).toBeVisible({ timeout: 20_000 });
  await expect(
    strip.getByTestId("bench-strip-ws-status"),
    `${label} bench strip should be connected to runtime.v2 WebSocket`,
  ).toHaveAttribute("data-ws-live", "true", { timeout: 20_000 });
  await expect(
    strip.getByTestId("bench-strip-last-event"),
    `${label} should render the marker pushed after the surface was mounted`,
  ).toContainText(marker, { timeout: 20_000 });
}

async function assertSurfaceReceivesBenchPush(
  window: Page,
  sessionId: string,
  surface: string,
  index: number,
  testInfo: TestInfo,
  frames: WebSocketFrameCapture[],
): Promise<string> {
  const marker = await publishBenchMarker(window, sessionId, surface, index);
  try {
    await expectBenchRealtimeMarker(window, sessionId, marker, surface);
  } catch (error) {
    await attachWebSocketFrames(testInfo, surface, frames);
    const relevantFrames = frames
      .filter(
        (frame) =>
          frame.payload.includes(sessionId) ||
          frame.payload.includes(marker) ||
          frame.payload.includes("SUBSCRIBE") ||
          frame.payload.includes("UNSUBSCRIBE") ||
          frame.payload.includes("ACK"),
      )
      .slice(-30);
    console.log(
      JSON.stringify(
        {
          surface,
          marker,
          relevant_frame_count: relevantFrames.length,
          relevant_frames: relevantFrames,
        },
        null,
        2,
      ),
    );
    throw error;
  }
  return marker;
}

test("main, Factory, PM, Chief Engineer, Director, and ContextOS receive live bench events over Nats-JetStream runtime.v2", async ({ window }, testInfo) => {
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
  const networkAudit = startRealtimeNetworkAudit(window);
  const wsCapture = await startWebSocketFrameCapture(window);
  await testInfo.attach("renderer-websocket-capture-ready", {
    body: JSON.stringify({ ready: true }),
    contentType: "application/json",
  });

  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();
  await waitForWebSocketPayload(
    wsCapture.frames,
    (payload) => payload.includes("\"type\": \"SUBSCRIBED\"") && payload.includes("\"event.bench\""),
    "runtime.v2 event.bench subscription",
  );

  const sessionId = `e2e-bench-${Date.now()}`;
  await backendJson(window, "/v2/factory/bench/sessions", {
    method: "POST",
    body: {
      session_id: sessionId,
      work_dir: `/tmp/${sessionId}`,
      project_ids: ["L1-01", "L2-01", "L3-01", "L4-01", "L5-01", "L6-01", "L7-01", "L8-01"],
      total: 8,
      metadata: {
        source: "playwright_realtime_nat_jetstream_workspaces",
      },
    },
  });

  const evidence: Array<{ surface: string; marker: string }> = [];

  evidence.push({
    surface: "main",
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "main", 1, testInfo, wsCapture.frames),
  });
  await testInfo.attach("main-bench-realtime", {
    body: await window.screenshot({ fullPage: true }),
    contentType: "image/png",
  });

  await window.locator("button[title*='Factory 模式']").click();
  await expect(window.getByTestId("factory-layered-layout")).toBeVisible();
  evidence.push({
    surface: "factory",
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "factory", 2, testInfo, wsCapture.frames),
  });
  await testInfo.attach("factory-bench-realtime", {
    body: await window.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
  await window.getByLabel("返回主界面").click();
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();

  await enterPmWorkspace(window);
  await expect(window.getByTestId("pm-workspace")).toBeVisible();
  evidence.push({
    surface: "pm",
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "pm", 3, testInfo, wsCapture.frames),
  });
  await testInfo.attach("pm-bench-realtime", {
    body: await window.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
  await backToMain(window, "pm-workspace-back");

  await enterChiefEngineerWorkspace(window);
  await expect(window.getByTestId("chief-engineer-workspace")).toBeVisible();
  evidence.push({
    surface: "chief-engineer",
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "chief-engineer", 4, testInfo, wsCapture.frames),
  });
  await testInfo.attach("chief-engineer-bench-realtime", {
    body: await window.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
  await backToMain(window, "chief-engineer-workspace-back");

  await enterDirectorWorkspace(window);
  await expect(window.getByTestId("director-workspace")).toBeVisible();
  evidence.push({
    surface: "director",
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "director", 5, testInfo, wsCapture.frames),
  });
  await testInfo.attach("director-bench-realtime", {
    body: await window.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
  await backToMain(window, "director-workspace-back");

  await enterContextOS(window);
  await expect(window.getByTestId("contextos-workspace")).toBeVisible();
  evidence.push({
    surface: "contextos",
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "contextos", 6, testInfo, wsCapture.frames),
  });
  await testInfo.attach("contextos-bench-realtime", {
    body: await window.screenshot({ fullPage: true }),
    contentType: "image/png",
  });

  await testInfo.attach("nats-jetstream-workspace-realtime-evidence.json", {
    body: JSON.stringify(
      {
        session_id: sessionId,
        transport: "Nats-JetStream runtime.v2 WebSocket",
        surfaces: evidence,
      },
      null,
      2,
    ),
    contentType: "application/json",
  });
  await attachWebSocketFrames(testInfo, "complete", wsCapture.frames);
  await wsCapture.detach();
  await attachRealtimeNetworkAudit(testInfo, networkAudit);
  networkAudit.detach();

  assertRealtimeNetworkAudit(networkAudit, wsCapture.frames);
  expect(pageErrors, "pageerror should remain empty during Nats-JetStream workspace audit").toEqual([]);
  expect(consoleErrors.filter((entry) => !/Unable to preload CSS/i.test(entry)), "console errors should remain empty").toEqual([]);
});
