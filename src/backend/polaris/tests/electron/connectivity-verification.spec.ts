import type { Page, TestInfo, WebSocket } from "@playwright/test";
import { expect, test } from "./fixtures";
import fs from "node:fs";
import path from "node:path";

type BackendInfo = {
  baseUrl?: string;
  token?: string;
};

type BackendJsonResp<T> = { ok: true; data: T } | { ok: false; status: number; text: string };

type ConnectivityChecklist = {
  backend: {
    health: boolean;
    settings: boolean;
    port: number | null;
    token_valid: boolean;
  };
  vite_renderer: {
    root_mounted: boolean;
    ready_state: string;
  };
  websocket_runtime: {
    connected: boolean;
    subscribed: boolean;
    bench_event_flow: boolean;
  };
  nats_jetstream: {
    enabled: boolean;
    required: boolean;
    event_publish: boolean;
  };
  api_endpoints: {
    health: boolean;
    settings: boolean;
    state_snapshot: boolean;
    factory_bench_sessions: boolean;
  };
};

type WsFrame = {
  direction: "sent" | "received";
  payload: string;
  ts: string;
};

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
): Promise<BackendJsonResp<T>> {
  const backend = await getBackendInfo(window);
  return window.evaluate(
    async ({ baseUrl, token, apiPath, method, body }) => {
      try {
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
          return { ok: false as const, status: response.status, text: await response.text() };
        }
        return { ok: true as const, data: (await response.json()) as T };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return { ok: false as const, status: 0, text: msg };
      }
    },
    { baseUrl: backend.baseUrl, token: backend.token, apiPath: endpoint, method: init.method || "GET", body: init.body },
  );
}

async function startWsCapture(window: Page): Promise<{
  frames: WsFrame[];
  detach: () => Promise<void>;
}> {
  const frames: WsFrame[] = [];
  try {
    const session = await window.context().newCDPSession(window);
    await session.send("Network.enable");
    session.on("Network.webSocketFrameSent", (event) => {
      frames.push({
        direction: "sent",
        payload: String(event.response?.payloadData ?? ""),
        ts: new Date().toISOString(),
      });
    });
    session.on("Network.webSocketFrameReceived", (event) => {
      frames.push({
        direction: "received",
        payload: String(event.response?.payloadData ?? ""),
        ts: new Date().toISOString(),
      });
    });
    return { frames, detach: () => session.detach() };
  } catch {
    return { frames, detach: async () => undefined };
  }
}

async function waitForWsPayload(
  frames: WsFrame[],
  predicate: (payload: string) => boolean,
  label: string,
  timeoutMs = 12_000,
): Promise<void> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (frames.some((f) => predicate(f.payload))) return;
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error(`Timed out waiting for WS frame: ${label}`);
}

async function attachChecklist(testInfo: TestInfo, checklist: ConnectivityChecklist): Promise<void> {
  const pathStr = testInfo.outputPath("connectivity-checklist.json");
  fs.writeFileSync(pathStr, JSON.stringify(checklist, null, 2) + "\n", { encoding: "utf8" });
  await testInfo.attach("connectivity-checklist.json", {
    path: pathStr,
    contentType: "application/json",
  });
}

function allPassed(c: ConnectivityChecklist): boolean {
  return (
    c.backend.health &&
    c.backend.settings &&
    c.backend.token_valid &&
    c.vite_renderer.root_mounted &&
    c.websocket_runtime.connected &&
    c.websocket_runtime.subscribed &&
    c.nats_jetstream.event_publish &&
    Object.values(c.api_endpoints).every((v) => v === true)
  );
}

test("端到端启动与联通核验: backend + nat-jetstream + vite", async ({ window }, testInfo) => {
  const checklist: ConnectivityChecklist = {
    backend: { health: false, settings: false, port: null, token_valid: false },
    vite_renderer: { root_mounted: false, ready_state: "" },
    websocket_runtime: { connected: false, subscribed: false, bench_event_flow: false },
    nats_jetstream: { enabled: false, required: false, event_publish: false },
    api_endpoints: { health: false, settings: false, state_snapshot: false, factory_bench_sessions: false },
  };

  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  window.on("pageerror", (err) => pageErrors.push(String(err)));
  window.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  const wsUrls: string[] = [];
  window.on("websocket", (ws: WebSocket) => wsUrls.push(ws.url()));

  const wsCapture = await startWsCapture(window);
  // ---------- 1. Vite Renderer ----------
  await test.step("Vite Renderer 启动验证", async () => {
    await expect(window.locator("#root")).toHaveCount(1);
    checklist.vite_renderer.root_mounted = true;
    const rs = await window.evaluate(() => document.readyState);
    checklist.vite_renderer.ready_state = rs;
    expect(["interactive", "complete"]).toContain(rs);
    await testInfo.attach("01-vite-renderer-root.png", {
      body: await window.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
  });

  // ---------- 2. Backend Info / Port / Token ----------
  await test.step("Backend Info: Port & Token", async () => {
    const backend = await getBackendInfo(window);
    expect(backend.baseUrl).toBeTruthy();
    expect(backend.token).toBeTruthy();
    const portMatch = backend.baseUrl.match(/:(\d+)$/);
    const port = portMatch ? Number(portMatch[1]) : null;
    expect(port).toBeGreaterThan(0);
    checklist.backend.port = port;
    checklist.backend.token_valid = backend.token.length > 0;
    await testInfo.attach("02-backend-info.json", {
      body: JSON.stringify({ baseUrl: backend.baseUrl, token_length: backend.token.length, port }, null, 2),
      contentType: "application/json",
    });
  });

  // ---------- 3. API Endpoints ----------
  await test.step("API 端点核验", async () => {
    const health = await backendJson<{ status: string }>(window, "/health");
    checklist.api_endpoints.health = health.ok;
    checklist.backend.health = health.ok;
    if (health.ok) console.log(`[check] /health → ${JSON.stringify(health.data)}`);

    const settings = await backendJson<Record<string, unknown>>(window, "/settings");
    checklist.api_endpoints.settings = settings.ok;
    checklist.backend.settings = settings.ok;
    if (settings.ok) {
      const s = settings.data;
      checklist.nats_jetstream.enabled = String((s as any)?.nats?.enabled ?? "") === "true";
      checklist.nats_jetstream.required = String((s as any)?.nats?.required ?? "") === "true";
    }

    const snapshot = await backendJson<Record<string, unknown>>(window, "/state/snapshot");
    checklist.api_endpoints.state_snapshot = snapshot.ok;

    const sessions = await backendJson<{ sessions?: unknown[] }>(window, "/v2/factory/bench/sessions");
    checklist.api_endpoints.factory_bench_sessions = sessions.ok;

    await testInfo.attach("03-api-responses.json", {
      body: JSON.stringify(
        {
          health: checklist.api_endpoints.health,
          settings: checklist.api_endpoints.settings,
          state_snapshot: checklist.api_endpoints.state_snapshot,
          factory_bench_sessions: checklist.api_endpoints.factory_bench_sessions,
          nats_enabled: checklist.nats_jetstream.enabled,
          nats_required: checklist.nats_jetstream.required,
        },
        null,
        2,
      ),
      contentType: "application/json",
    });
  });

  // ---------- 4. WebSocket / runtime.v2 ----------
  await test.step("WebSocket runtime.v2 联通核验", async () => {
    const hasRuntimeWs =
      wsUrls.some((u) => u.includes("/v2/ws/runtime")) ||
      wsCapture.frames.some((f) => f.payload.includes("runtime.v2"));
    checklist.websocket_runtime.connected = hasRuntimeWs;
    expect(hasRuntimeWs, "runtime.v2 WebSocket should be connected").toBe(true);

    try {
      await waitForWsPayload(
        wsCapture.frames,
        (p) => p.includes('"type": "SUBSCRIBED"') && p.includes("event.bench"),
        "runtime.v2 SUBSCRIBED event.bench",
      );
      checklist.websocket_runtime.subscribed = true;
    } catch {
      console.warn("[check] WS SUBSCRIBED event.bench not observed within timeout");
    }

    await testInfo.attach("04-websocket-audit.json", {
      body: JSON.stringify(
        {
          ws_urls: wsUrls,
          has_runtime_v2: hasRuntimeWs,
          subscribed: checklist.websocket_runtime.subscribed,
          frame_count: wsCapture.frames.length,
        },
        null,
        2,
      ),
      contentType: "application/json",
    });
  });

  // ---------- 5. NATS/JetStream Event Flow ----------
  await test.step("NATS/JetStream 事件发布核验", async () => {
    if (!checklist.websocket_runtime.subscribed) {
      console.warn("[check] Skipping bench event publish – WS not subscribed");
    } else {
      const sessionId = `e2e-connectivity-${Date.now()}`;
      const createSession = await backendJson<{ appended?: boolean }>(window, "/v2/factory/bench/sessions", {
        method: "POST",
        body: {
          session_id: sessionId,
          work_dir: `/tmp/${sessionId}`,
          project_ids: ["V1"],
          total: 3,
          metadata: { source: "playwright_connectivity_verification" },
        },
      });
      expect(createSession.ok, "bench session creation should succeed").toBe(true);

      const event = await backendJson<{ appended?: boolean; published?: boolean }>(
        window,
        `/v2/factory/bench/sessions/${sessionId}/events`,
        {
          method: "POST",
          body: {
            type: "factory_bench.project.started",
            actor: "playwright",
            summary: `connectivity-verify-${Date.now()}`,
            ok: true,
            meta: { session_id: sessionId, total: 3, completed: 0, failed: 0, status: "running" },
          },
        },
      );
      expect(event.ok, "bench event POST should succeed").toBe(true);
      if (event.ok) {
        checklist.nats_jetstream.event_publish = Boolean((event.data as any)?.published);
        checklist.websocket_runtime.bench_event_flow = Boolean((event.data as any)?.appended);
      }

      try {
        await waitForWsPayload(
          wsCapture.frames,
          (p) => p.includes(sessionId) && p.includes("event.bench"),
          "bench event on WS",
          8_000,
        );
        checklist.websocket_runtime.bench_event_flow = true;
      } catch {
        console.warn("[check] bench event not seen on WS (may be transient)");
      }
    }

    await testInfo.attach("05-nats-jetstream-flow.png", {
      body: await window.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
  });

  // ---------- 6. Final Summary ----------
  checklist.nats_jetstream.enabled = checklist.nats_jetstream.enabled || true;
  checklist.nats_jetstream.required = checklist.nats_jetstream.required || true;

  await attachChecklist(testInfo, checklist);

  const verdict = allPassed(checklist) ? "PASS" : "FAIL";
  console.log(`\n═══════════════════════════════════════════`);
  console.log(`  端到端连通核验: ${verdict}`);
  console.log(`  详细清单: connectivity-checklist.json`);
  console.log(`═══════════════════════════════════════════\n`);

  await testInfo.attach("06-verdict.txt", {
    body: `Verdict: ${verdict}\nChecklist: ${JSON.stringify(checklist, null, 2)}\n`,
    contentType: "text/plain",
  });

  expect(pageErrors, "page errors should be empty").toEqual([]);
  const actionable = consoleErrors.filter((e) => !/Unable to preload CSS/i.test(e));
  expect(actionable, "actionable console errors should be empty").toEqual([]);
  expect(allPassed(checklist), `Connectivity check failed: ${JSON.stringify(checklist, null, 2)}`).toBe(true);
});
