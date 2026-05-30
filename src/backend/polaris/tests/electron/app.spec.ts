import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect } from "./fixtures";

test("app launches and renders", async ({ window }) => {
  await expect(window.locator("#root")).toHaveCount(1);
  const readyState = await window.evaluate(() => document.readyState);
  expect(["interactive", "complete"]).toContain(readyState);
});

test("backend responds to settings", async ({ window }) => {
  const status = await window.evaluate(async () => {
    const api = (window as any).polaris;
    if (!api) {
      return { ok: false, error: "polaris API missing" };
    }
    const backend = await api.getBackendInfo();
    if (!backend?.baseUrl || !backend?.token) {
      return { ok: false, error: "backend info missing" };
    }
    const resp = await fetch(`${backend.baseUrl}/settings`, {
      headers: { authorization: `Bearer ${backend.token}` },
    });
    return { ok: resp.ok, status: resp.status };
  });

  expect(status.ok).toBeTruthy();
});

test("backend settings switch persists workspace in Electron", async ({ window, testEnv }) => {
  const targetWorkspace = fs.mkdtempSync(path.join(os.tmpdir(), "polaris-electron-settings-"));
  const settingsHome = testEnv.settingsHome;
  expect(settingsHome, "settings home should be known").not.toBe("");
  const persistedPath = path.join(settingsHome, "config", "settings.json");
  const originalPersistedSettings = testEnv.useRealSettings && fs.existsSync(persistedPath)
    ? fs.readFileSync(persistedPath, "utf-8")
    : null;

  try {
    const result = await window.evaluate(async ({ workspace }) => {
      const api = (window as any).polaris;
      if (!api) {
        return { ok: false, error: "polaris API missing" };
      }
      const backend = await api.getBackendInfo();
      if (!backend?.baseUrl || !backend?.token) {
        return { ok: false, error: "backend info missing" };
      }

      const headers = {
        authorization: `Bearer ${backend.token}`,
        "Content-Type": "application/json",
      };
      const post = await fetch(`${backend.baseUrl}/settings`, {
        method: "POST",
        headers,
        cache: "no-store",
        body: JSON.stringify({ workspace, pm_runs_director: true }),
      });
      const postText = await post.text();
      const postJson = postText ? JSON.parse(postText) : {};

      const get = await fetch(`${backend.baseUrl}/settings`, {
        headers,
        cache: "no-store",
      });
      const getText = await get.text();
      const getJson = getText ? JSON.parse(getText) : {};

      return {
        ok: post.ok && get.ok,
        postStatus: post.status,
        getStatus: get.status,
        postWorkspace: String(postJson.workspace || ""),
        getWorkspace: String(getJson.workspace || ""),
        baseUrl: String(backend.baseUrl || ""),
      };
    }, { workspace: targetWorkspace });

    expect(result.ok, JSON.stringify(result)).toBe(true);
    expect(result.postWorkspace.toLowerCase()).toBe(targetWorkspace.toLowerCase());
    expect(result.getWorkspace.toLowerCase()).toBe(targetWorkspace.toLowerCase());

    const persisted = JSON.parse(fs.readFileSync(persistedPath, "utf-8")) as { workspace?: string };
    expect(String(persisted.workspace || "").toLowerCase()).toBe(targetWorkspace.toLowerCase());
  } finally {
    if (testEnv.useRealSettings) {
      if (originalPersistedSettings !== null) {
        fs.mkdirSync(path.dirname(persistedPath), { recursive: true });
        fs.writeFileSync(persistedPath, originalPersistedSettings, { encoding: "utf8" });
      } else {
        fs.rmSync(persistedPath, { force: true });
      }
    }
    fs.rmSync(targetWorkspace, { recursive: true, force: true });
  }
});
