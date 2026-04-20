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
