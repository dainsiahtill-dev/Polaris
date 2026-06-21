import { promises as fs } from "node:fs";
import path from "node:path";
import { expect, test } from "./fixtures";

type BackendInfo = {
  baseUrl?: string;
  token?: string;
};

type RuntimeLayoutPayload = {
  runtime_root?: string;
};

type RuntimeStatusPayload = {
  type?: string;
  engine_status?: {
    running?: boolean;
    phase?: string;
    stale?: boolean;
    orphaned?: boolean;
    recovery_code?: string;
    error?: string;
  } | null;
};

declare global {
  interface Window {
    __polarisEngineFailureSeen?: boolean;
    __polarisEngineFailureObserver?: MutationObserver;
  }
}

async function getBackendInfo(window: import("@playwright/test").Page): Promise<Required<BackendInfo>> {
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
  return { baseUrl: info.baseUrl, token: info.token };
}

async function requestJson<T>(window: import("@playwright/test").Page, endpoint: string): Promise<T> {
  const backend = await getBackendInfo(window);
  return window.evaluate(
    async ({ baseUrl, token, apiPath }) => {
      const response = await fetch(`${baseUrl}${apiPath}`, {
        cache: "no-store",
        headers: {
          authorization: `Bearer ${token}`,
          "Cache-Control": "no-store",
          Pragma: "no-cache",
        },
      });
      if (!response.ok) {
        throw new Error(`fetch ${apiPath} failed: ${response.status}`);
      }
      return (await response.json()) as unknown;
    },
    { baseUrl: backend.baseUrl, token: backend.token, apiPath: endpoint },
  ) as Promise<T>;
}

async function requestRuntimeStatus(window: import("@playwright/test").Page): Promise<RuntimeStatusPayload> {
  const backend = await getBackendInfo(window);
  return window.evaluate(
    async ({ baseUrl, token }) => {
      const wsUrl = `${String(baseUrl).replace(/^http/i, "ws")}/v2/ws/runtime?token=${encodeURIComponent(token)}`;
      return await new Promise<RuntimeStatusPayload>((resolve, reject) => {
        const socket = new WebSocket(wsUrl);
        const timeout = window.setTimeout(() => {
          socket.close();
          reject(new Error("timed out waiting for runtime status"));
        }, 15_000);

        socket.onopen = () => {
          socket.send(JSON.stringify({ type: "GET_STATUS", roles: ["pm", "director", "qa"] }));
        };
        socket.onerror = () => {
          window.clearTimeout(timeout);
          reject(new Error("runtime websocket error"));
        };
        socket.onmessage = (event) => {
          const payload = JSON.parse(String(event.data || "{}")) as RuntimeStatusPayload;
          if (payload.type !== "status" || !("engine_status" in payload)) {
            return;
          }
          window.clearTimeout(timeout);
          socket.close();
          resolve(payload);
        };
      });
    },
    backend,
  );
}

async function installFailureObserver(window: import("@playwright/test").Page): Promise<void> {
  await window.evaluate(() => {
    window.__polarisEngineFailureSeen = false;
    window.__polarisEngineFailureObserver?.disconnect();

    const check = () => {
      const dialog = document.querySelector("[role='alertdialog']");
      if (dialog?.textContent?.includes("Polaris 引擎执行失败")) {
        window.__polarisEngineFailureSeen = true;
      }

      const overlay = document.querySelector("[data-testid='llm-runtime-overlay']");
      if (overlay?.textContent?.includes("执行失败")) {
        window.__polarisEngineFailureSeen = true;
      }
    };

    const observer = new MutationObserver(check);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    window.__polarisEngineFailureObserver = observer;
    check();
  });
}

test("stale in-flight engine status is recovered without a user-facing failure card", async ({ window }) => {
  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });

  const layout = await requestJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
  const runtimeRoot = String(layout.runtime_root || "").trim();
  expect(runtimeRoot).not.toBe("");

  await installFailureObserver(window);

  const engineStatusPath = path.join(runtimeRoot, "status", "engine.status.json");
  await fs.mkdir(path.dirname(engineStatusPath), { recursive: true });
  await fs.writeFile(
    engineStatusPath,
    `${JSON.stringify(
      {
        schema_version: 1,
        running: true,
        phase: "dispatching",
        run_id: "pm-stale-ui",
        pm_iteration: 1,
        roles: {
          PM: {
            status: "dispatching",
            running: true,
            detail: "PM contract persisted; dispatching Chief Engineer handoff and Director execution",
            updated_at: "2026-05-31T10:53:57Z",
          },
          Director: {
            status: "idle",
            running: false,
            detail: "Waiting for PM dispatch",
            updated_at: "2026-05-31T10:53:57Z",
          },
        },
        summary: {},
        updated_at: "2026-05-31T10:53:57Z",
        error: "",
      },
      null,
      2,
    )}\n`,
    "utf-8",
  );

  const status = await requestRuntimeStatus(window);
  expect(status.engine_status).toMatchObject({
    running: false,
    phase: "idle",
    stale: true,
    orphaned: true,
    recovery_code: "ENGINE_ORPHANED",
    error: "",
  });

  await window.waitForTimeout(6_000);
  const failureSeen = await window.evaluate(() => Boolean(window.__polarisEngineFailureSeen));
  expect(failureSeen).toBe(false);
  await expect(window.getByRole("alertdialog", { name: "Polaris 引擎执行失败" })).toHaveCount(0);
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
});
