import fs from "fs";
import type { Locator, Page, Route, TestInfo } from "@playwright/test";
import { expect, test } from "./fixtures";

process.env.KERNELONE_E2E_SHOW_WINDOW = process.env.KERNELONE_E2E_SHOW_WINDOW || "1";

const ignoredConsoleErrorPatterns = [
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /Unable to preload CSS for \/assets\//i,
];

async function attachScreenshot(window: Page, testInfo: TestInfo, name: string): Promise<void> {
  const screenshotPath = testInfo.outputPath(`${name}.png`);
  await window.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach(name, { path: screenshotPath, contentType: "image/png" });
  expect(fs.existsSync(screenshotPath)).toBe(true);
}

async function openSettings(window: Page): Promise<void> {
  const testIdEntry = window.getByTestId("control-panel-open-settings");
  if (await testIdEntry.isVisible().catch(() => false)) {
    await testIdEntry.click();
    return;
  }

  await window.locator("button[title='Settings'], button[title*='系统配置'], button[title*='设置']").first().click();
}

async function openMoreMenu(window: Page): Promise<void> {
  await window.getByTestId("control-panel-more-menu").click();
  await expect(window.getByRole("menu")).toBeVisible();
}

async function enterPmWorkspace(window: Page): Promise<void> {
  await openMoreMenu(window);
  await window.getByTestId("enter-pm-workspace").click();
}

async function expectNoDocumentHorizontalOverflow(window: Page, label: string): Promise<void> {
  const metrics = await window.evaluate(() => ({
    bodyScrollWidth: document.body.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
  }));
  const scrollWidth = Math.max(metrics.bodyScrollWidth, metrics.documentScrollWidth);
  expect(
    scrollWidth,
    `${label} should not create page-level horizontal overflow: ${JSON.stringify(metrics)}`,
  ).toBeLessThanOrEqual(metrics.clientWidth + 4);
}

async function expectContained(locator: Locator, label: string): Promise<void> {
  await expect(locator).toBeVisible();
  const metrics = await locator.evaluate((element) => {
    const html = element as HTMLElement;
    return {
      clientWidth: html.clientWidth,
      scrollWidth: html.scrollWidth,
      clientHeight: html.clientHeight,
      scrollHeight: html.scrollHeight,
    };
  });
  expect(metrics.scrollWidth, `${label} should not overflow horizontally`).toBeLessThanOrEqual(metrics.clientWidth + 4);
}

async function expectDirectChildrenDoNotOverlap(locator: Locator, label: string): Promise<void> {
  await expect(locator).toBeVisible();
  const overlaps = await locator.evaluate((element) => {
    const children = Array.from((element as HTMLElement).children)
      .map((child) => {
        const rect = child.getBoundingClientRect();
        return {
          text: (child.textContent || "").trim(),
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
        };
      })
      .filter((rect) => rect.width > 1 && rect.height > 1);

    const findings: Array<{ a: string; b: string }> = [];
    for (let outer = 0; outer < children.length; outer += 1) {
      for (let inner = outer + 1; inner < children.length; inner += 1) {
        const a = children[outer];
        const b = children[inner];
        const separated =
          a.right <= b.left + 1 ||
          b.right <= a.left + 1 ||
          a.bottom <= b.top + 1 ||
          b.bottom <= a.top + 1;
        if (!separated) {
          findings.push({ a: a.text, b: b.text });
        }
      }
    }
    return findings;
  });
  expect(overlaps, `${label} direct children should not overlap`).toEqual([]);
}

async function expectRoleSessionToolbarReadable(window: Page, label: string): Promise<void> {
  const strip = window.getByTestId("ai-role-session-strip");
  const statusRow = window.getByTestId("ai-role-session-status-row");
  const actionRow = window.getByTestId("ai-role-session-actions");

  await expect(strip, `${label} RoleSession strip should be visible`).toBeVisible();
  await expect(actionRow, `${label} RoleSession actions should be visible`).toBeVisible();
  await expectContained(strip, `${label} AI RoleSession strip`);
  await expectDirectChildrenDoNotOverlap(statusRow, `${label} AI RoleSession status row`);
  await expectDirectChildrenDoNotOverlap(actionRow, `${label} AI RoleSession action row`);

  const buttonMetrics = await actionRow.evaluate((element) =>
    Array.from(element.querySelectorAll("button")).map((button) => {
      const rect = button.getBoundingClientRect();
      return {
        ariaLabel: button.getAttribute("aria-label") || button.getAttribute("title") || "",
        text: (button.textContent || "").trim(),
        width: rect.width,
        height: rect.height,
      };
    }),
  );
  expect(buttonMetrics.length, `${label} action toolbar should expose icon buttons`).toBeGreaterThanOrEqual(4);
  expect(
    buttonMetrics.filter((item) => item.text),
    `${label} action toolbar must stay icon-only to prevent label collisions`,
  ).toEqual([]);
  expect(
    buttonMetrics.filter((item) => item.width > 34 || item.height > 34),
    `${label} action toolbar icon buttons should keep compact fixed dimensions`,
  ).toEqual([]);
  await expectNoDocumentHorizontalOverflow(window, `${label} PM AI assistant role session strip`);
}

async function installLlmDiagnosticsRoutes(window: Page): Promise<void> {
  const llmConfig = {
    schema_version: 1,
    providers: {
      "qwen-main": {
        type: "openai_compat",
        name: "Qwen Production Beijing Token Plan Provider With Long Billing Alias",
        model: "qwen3-max-current-with-long-region-routing-label",
        base_url: "https://token-plan.example.invalid/compatible-mode/v1",
      },
    },
    roles: {
      pm: {
        provider_id: "qwen-main",
        model: "qwen3-max-current-with-long-region-routing-label",
      },
      director: {
        provider_id: "qwen-main",
        model: "qwen3-max-current-with-long-region-routing-label",
      },
    },
  };

  const llmStatus = {
    state: "BLOCKED",
    required_ready_roles: ["pm", "director"],
    blocked_roles: ["pm"],
    unsupported_roles: [],
    roles: {
      pm: {
        provider_id: "qwen-main",
        model: "qwen3-max-current-with-long-region-routing-label",
        ready: false,
        runtime_supported: true,
        readiness_issue: "model_mismatch",
        tested_provider_id: "qwen-main",
        tested_model: "qwen3-max-previously-tested-model",
        tested_timestamp: "2026-05-29T19:30:00Z",
      },
    },
    providers: {
      "qwen-main": {
        ready: false,
        suites: {
          connectivity: { ok: false },
        },
      },
    },
  };

  const providerInfo = {
    name: "OpenAI Compatible",
    type: "openai_compat",
    description: "Compatible HTTP provider with manual model configuration.",
    version: "1.0.0",
    author: "Polaris",
    documentation_url: "",
    supported_features: ["chat", "tool_calling"],
    cost_class: "METERED",
    provider_category: "cloud",
    autonomous_file_access: false,
    requires_file_interfaces: false,
    model_listing_method: "manual",
  };

  await window.route("**/v2/llm/config**", async (route: Route) => {
    if (route.request().method().toUpperCase() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(llmConfig) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, config: llmConfig }) });
  });
  await window.route("**/v2/llm/status**", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(llmStatus) });
  });
  await window.route("**/v2/llm/providers", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ providers: [providerInfo] }) });
  });
  await window.route("**/v2/llm/providers/openai_compat/config", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ type: "openai_compat", name: "OpenAI Compatible", model: "gpt-5.3-codex" }),
    });
  });
}

async function installReadyLlmStatusRoute(window: Page): Promise<void> {
  const readyStatus = {
    state: "READY",
    required_ready_roles: ["pm", "director"],
    blocked_roles: [],
    unsupported_roles: [],
    roles: {
      pm: {
        provider_id: "codex_cli",
        model: "gpt-5.3-codex",
        ready: true,
        runtime_supported: true,
        readiness_issue: "",
        tested_provider_id: "codex_cli",
        tested_model: "gpt-5.3-codex",
        tested_timestamp: "2026-05-30T17:33:43Z",
      },
      director: {
        provider_id: "codex_cli",
        model: "gpt-5.3-codex",
        ready: true,
        runtime_supported: true,
        readiness_issue: "",
        tested_provider_id: "codex_cli",
        tested_model: "gpt-5.3-codex",
        tested_timestamp: "2026-05-30T17:33:56Z",
      },
    },
    providers: {
      codex_cli: {
        ready: true,
        grade: "PASS",
        model: "gpt-5.3-codex",
        role: "pm",
      },
    },
  };

  await window.unroute("**/v2/llm/status**");
  await window.route("**/v2/llm/status**", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(readyStatus) });
  });
}

async function installRoleSessionRoutes(window: Page): Promise<void> {
  await window.route("**/v2/role/pm/chat/status**", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ready: true,
        configured: true,
        role: "pm",
        role_config: {
          provider_id: "codex_cli",
          model: "gpt-5.3-codex",
        },
      }),
    });
  });
  await window.route("**/v2/conversations**", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ conversations: [], total: 0 }) });
  });
  await window.route("**/v2/roles/capabilities/pm**", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        role: "pm",
        capabilities: {
          electron_workbench: ["execute_tools", "manage_workers", "read_files", "write_tasks"],
        },
      }),
    });
  });
  await window.route("**/v2/roles/sessions**", async (route: Route) => {
    const url = new URL(route.request().url());
    const method = route.request().method().toUpperCase();
    const sessionId = "session-ui-toolbar-long-status";

    if (url.pathname === "/v2/roles/sessions" && method === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, session: { id: sessionId } }),
      });
      return;
    }

    if (url.pathname === `/v2/roles/sessions/${sessionId}`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          session: {
            id: sessionId,
            title: "PM role session for dense toolbar visual audit",
            role: "pm",
            host_kind: "electron_workbench",
            attachment_mode: "attached_readonly",
            attached_task_id: "PM-E2E-UI-TOOLBAR-LONG-TASK-ID",
            state: "active",
            message_count: 0,
            updated_at: "2026-05-30T08:00:00Z",
          },
        }),
      });
      return;
    }

    if (url.pathname === `/v2/roles/sessions/${sessionId}/actions/attach`) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
      return;
    }

    await route.continue();
  });
}

test("LLM diagnostics and role session toolbar stay readable in dense desktop layouts", async ({ window }, testInfo) => {
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

  await window.setViewportSize({ width: 1180, height: 720 });
  await installLlmDiagnosticsRoutes(window);
  await installRoleSessionRoutes(window);
  await window.reload();
  await expect(window.locator("#root")).toHaveCount(1);

  await openSettings(window);
  await expect(window.getByText("系统配置")).toBeVisible();
  await window.getByTestId("settings-tab-llm").click();
  await expect(window.getByTestId("llm-readiness-diagnostics")).toBeVisible();
  const qwenDiagnostic = window.getByTestId("llm-readiness-diagnostic-row").filter({ hasText: "qwen-main" }).first();
  await expect(qwenDiagnostic).toBeVisible();
  await expect(qwenDiagnostic.getByTestId("llm-readiness-diagnostic-provider")).toContainText("Qwen Production Beijing Token Plan Provider");
  await expect(qwenDiagnostic.getByTestId("llm-readiness-diagnostic-provider")).toContainText("qwen-main");
  await expect(qwenDiagnostic.getByTestId("llm-readiness-diagnostic-model")).toContainText("qwen3-max-current-with-long-region-routing-label");
  await expect(qwenDiagnostic.getByTestId("llm-readiness-diagnostic-reason")).toContainText("最近通过测试的模型不是当前绑定模型");
  await expectContained(window.getByTestId("llm-readiness-summary"), "LLM readiness summary");
  await expectContained(qwenDiagnostic, "LLM readiness diagnostic row");
  await expectNoDocumentHorizontalOverflow(window, "LLM settings diagnostics");
  await attachScreenshot(window, testInfo, "llm-settings-provider-diagnostics");

  await window.getByRole("button", { name: "取消" }).click();
  await expect(window.getByText("系统配置")).toHaveCount(0);

  await installReadyLlmStatusRoute(window);
  await window.reload();
  await expect(window.locator("#root")).toHaveCount(1);

  await enterPmWorkspace(window);
  await expect(window.getByTestId("pm-workspace")).toBeVisible();
  await expect(window.getByText("PM 当前被阻塞")).toHaveCount(0);
  await expectRoleSessionToolbarReadable(window, "1180px");

  await window.setViewportSize({ width: 1024, height: 720 });
  await expectRoleSessionToolbarReadable(window, "1024px");
  await attachScreenshot(window, testInfo, "pm-ai-role-session-toolbar");

  expect(pageErrors, "renderer pageerror should remain empty").toEqual([]);
  expect(
    consoleErrors.filter((error) => !ignoredConsoleErrorPatterns.some((pattern) => pattern.test(error))),
    "actionable console errors should remain empty",
  ).toEqual([]);
});
