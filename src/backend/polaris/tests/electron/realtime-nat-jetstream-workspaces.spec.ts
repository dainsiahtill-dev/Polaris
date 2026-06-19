import type { Page } from "@playwright/test";
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
  const marker = `nat-jetstream-${surface}-${Date.now()}`;
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
): Promise<string> {
  const marker = await publishBenchMarker(window, sessionId, surface, index);
  await expectBenchRealtimeMarker(window, sessionId, marker, surface);
  return marker;
}

test("main, Factory, PM, Chief Engineer, and Director receive live bench events over Nat-JetStream runtime.v2", async ({ window }, testInfo) => {
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
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();

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
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "main", 1),
  });
  await testInfo.attach("main-bench-realtime", {
    body: await window.screenshot({ fullPage: true }),
    contentType: "image/png",
  });

  await window.locator("button[title*='Factory 模式']").click();
  await expect(window.getByTestId("factory-layered-layout")).toBeVisible();
  evidence.push({
    surface: "factory",
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "factory", 2),
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
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "pm", 3),
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
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "chief-engineer", 4),
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
    marker: await assertSurfaceReceivesBenchPush(window, sessionId, "director", 5),
  });
  await testInfo.attach("director-bench-realtime", {
    body: await window.screenshot({ fullPage: true }),
    contentType: "image/png",
  });

  await testInfo.attach("nat-jetstream-workspace-realtime-evidence.json", {
    body: JSON.stringify(
      {
        session_id: sessionId,
        transport: "Nat-JetStream runtime.v2 WebSocket",
        surfaces: evidence,
      },
      null,
      2,
    ),
    contentType: "application/json",
  });

  expect(pageErrors, "pageerror should remain empty during Nat-JetStream workspace audit").toEqual([]);
  expect(consoleErrors.filter((entry) => !/Unable to preload CSS/i.test(entry)), "console errors should remain empty").toEqual([]);
});
