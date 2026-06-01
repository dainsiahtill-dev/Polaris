import fs from "fs";
import type { Page, Route, TestInfo } from "@playwright/test";
import { expect, test } from "./fixtures";

const llmConfig = {
  schema_version: 1,
  providers: {
    "kimi-layout": {
      type: "anthropic_compat",
      name: "Kimi Coding Layout Verification Provider",
      model: "kimi-for-coding-long-layout-verification-model",
      base_url: "https://layout.example.invalid/v1",
      streaming: true,
    },
    "deepseek-layout": {
      type: "anthropic_compat",
      name: "DeepSeek-V4-Pro Layout Verification Provider",
      model: "deepseek-v4-pro-super-long-model-name",
      base_url: "https://layout.example.invalid/v1",
      streaming: true,
    },
  },
  roles: {
    pm: { provider_id: "deepseek-layout", model: "deepseek-v4-pro-super-long-model-name" },
    director: { provider_id: "kimi-layout", model: "kimi-for-coding-long-layout-verification-model" },
    qa: { provider_id: "deepseek-layout", model: "deepseek-v4-pro-super-long-model-name" },
    architect: { provider_id: "kimi-layout", model: "kimi-for-coding-long-layout-verification-model" },
  },
};

const llmStatus = {
  state: "BLOCKED",
  factory_state: "BLOCKED",
  required_ready_roles: ["pm", "director", "qa", "architect"],
  blocked_roles: ["director", "qa", "architect"],
  unsupported_roles: [],
  factory_blocked_roles: ["director", "qa", "architect"],
  factory_unsupported_roles: [],
  roles: {
    pm: {
      provider_id: "deepseek-layout",
      model: "deepseek-v4-pro-super-long-model-name",
      ready: false,
      grade: "WARN",
      readiness_issue: "model_mismatch",
      tested_provider_id: "kimi-layout",
      tested_model: "kimi-for-coding",
      tested_timestamp: "2026-05-30T10:00:00Z",
    },
    director: {
      provider_id: "kimi-layout",
      model: "kimi-for-coding-long-layout-verification-model",
      ready: true,
      grade: "PASS",
      tested_provider_id: "kimi-layout",
      tested_model: "kimi-for-coding-long-layout-verification-model",
      tested_timestamp: "2026-05-30T10:00:00Z",
    },
    qa: {
      provider_id: "deepseek-layout",
      model: "deepseek-v4-pro-super-long-model-name",
      ready: false,
      grade: "WARN",
      readiness_issue: "provider_not_ready",
      tested_provider_id: "kimi-layout",
      tested_model: "kimi-for-coding",
      tested_timestamp: "2026-05-30T10:00:00Z",
    },
    architect: {
      provider_id: "kimi-layout",
      model: "kimi-for-coding-long-layout-verification-model",
      ready: true,
      grade: "PASS",
      tested_provider_id: "kimi-layout",
      tested_model: "kimi-for-coding-long-layout-verification-model",
      tested_timestamp: "2026-05-30T10:00:00Z",
    },
  },
  providers: {
    "kimi-layout": { ready: true, grade: "PASS", suites: { connectivity: { ok: true } } },
    "deepseek-layout": { ready: false, grade: "WARN", suites: { connectivity: { ok: false } } },
  },
};

const providerInfos = [
  {
    name: "Anthropic Compatible",
    type: "anthropic_compat",
    description: "Compatible provider used for layout verification.",
    version: "1.0.0",
    author: "Polaris",
    supported_features: ["chat", "streaming", "thinking"],
    cost_class: "METERED",
    provider_category: "cloud",
    autonomous_file_access: false,
    requires_file_interfaces: false,
    model_listing_method: "manual",
  },
  {
    name: "OpenAI Compatible",
    type: "openai_compat",
    description: "Compatible provider used for layout verification.",
    version: "1.0.0",
    author: "Polaris",
    supported_features: ["chat", "streaming"],
    cost_class: "METERED",
    provider_category: "cloud",
    autonomous_file_access: false,
    requires_file_interfaces: false,
    model_listing_method: "manual",
  },
];

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

async function fulfillJson(route: Route, body: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify(body),
  });
}

async function installLlmDeepLayoutRoutes(window: Page): Promise<void> {
  await window.route("**/v2/llm/config**", async (route) => {
    const method = route.request().method().toUpperCase();
    await fulfillJson(route, method === "GET" ? llmConfig : { ok: true, config: llmConfig });
  });

  await window.route("**/v2/llm/status**", async (route) => fulfillJson(route, llmStatus));
  await window.route("**/v2/llm/providers", async (route) => fulfillJson(route, { providers: providerInfos }));
  await window.route("**/v2/llm/providers/*/config", async (route) =>
    fulfillJson(route, { type: "anthropic_compat", name: "Anthropic Compatible", model: "kimi-for-coding" }),
  );
  await window.route("**/v2/llm/providers/*/models", async (route) =>
    fulfillJson(route, {
      supported: true,
      models: ["kimi-for-coding-long-layout-verification-model", "deepseek-v4-pro-super-long-model-name"],
    }),
  );
  await window.route("**/llm/interview/cancel", async (route) => fulfillJson(route, { ok: true }));
  await window.route("**/llm/interview/stream", async (route) => {
    const thinking =
      "我会先识别风险分类，然后按影响范围、复现路径、修复收益和验证成本排序。这里故意放入较长内容，用来验证实时思考过程不会被挤压或隐藏。";
    const answer = [
      "建议从四层审计：入口参数、状态同步、异步流、持久化回写。",
      "第一，检查配置删除后 roles/provider 引用是否同时清理，避免旧 provider 被保存动作重新合并回来。",
      "第二，检查 SSE content_chunk 的标签解析，确认 thinking 和 answer 分区在分片、换行、CRLF 下都能实时更新。",
      "第三，检查设置面板布局，问答区必须拥有独立滚动容器，右侧日志必须留在 modal 内部。",
      "第四，把布局验收固化为 Playwright 尺寸门禁，避免再次依赖人工截图。",
    ].join("\n");
    const body = [
      sse("start", { session_id: "layout-session-1" }),
      sse("content_chunk", { content: `<thinking>${thinking.slice(0, 38)}`, timestamp: "2026-06-02T12:00:00.000Z" }),
      sse("content_chunk", {
        content: `${thinking.slice(38)}</thinking>\n<answer>${answer.slice(0, 80)}`,
        timestamp: "2026-06-02T12:00:00.100Z",
      }),
      sse("content_chunk", { content: answer.slice(80, 180), timestamp: "2026-06-02T12:00:00.200Z" }),
      sse("content_chunk", { content: `${answer.slice(180)}</answer>`, timestamp: "2026-06-02T12:00:00.300Z" }),
      sse("complete", { ok: true, sessionId: "layout-session-1", answer, thinking }),
    ].join("");

    await route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache",
      },
      body,
    });
  });
}

async function openSettings(window: Page): Promise<void> {
  const testIdEntry = window.getByTestId("control-panel-open-settings");
  if (await testIdEntry.isVisible().catch(() => false)) {
    await testIdEntry.click();
    return;
  }
  await window.locator("button[title='Settings'], button[title*='系统配置'], button[title*='设置']").first().click();
}

async function attachScreenshot(window: Page, testInfo: TestInfo, name: string): Promise<void> {
  const screenshotPath = testInfo.outputPath(`${name}.png`);
  await window.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach(name, { path: screenshotPath, contentType: "image/png" });
  expect(fs.existsSync(screenshotPath)).toBe(true);
}

test("LLM deep test layout keeps streaming panels, conversation, and logs contained", async ({ window }, testInfo) => {
  await window.setViewportSize({ width: 1989, height: 1031 });
  await installLlmDeepLayoutRoutes(window);
  await window.reload({ waitUntil: "domcontentloaded" });
  await expect(window.locator("#root")).toHaveCount(1);

  await openSettings(window);
  await window.getByTestId("settings-tab-llm").click();
  await window.locator("button").filter({ hasText: /深测|DEEP TEST/i }).first().click();
  await window.locator("button").filter({ hasText: /^\s*Director\s*/ }).first().click().catch(() => undefined);
  await window.locator("button").filter({ hasText: /Kimi Coding Layout Verification Provider/ }).first().click().catch(() => undefined);

  await window.locator('textarea[placeholder="在这里输入追问问题..."]').fill("请说明你如何在代码审查中发现风险问题，并提出改进建议。");
  await window.getByRole("button", { name: /发送追问/ }).click();
  await expect(window.getByText("问答 1")).toBeVisible();
  await expect(window.locator("text=建议从四层审计").first()).toBeVisible();

  const metrics = await window.evaluate(() => {
    function rectOf(selector: string): { right: number; bottom: number; height: number; scrollWidth: number; clientWidth: number } | null {
      const element = document.querySelector(selector) as HTMLElement | null;
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return {
        right: Math.round(rect.right),
        bottom: Math.round(rect.bottom),
        height: Math.round(rect.height),
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      };
    }

    const passButton = Array.from(document.querySelectorAll("button")).find((button) => (button.textContent || "").trim() === "通过") as HTMLElement | undefined;
    const sendButton = Array.from(document.querySelectorAll("button")).find((button) => (button.textContent || "").includes("发送追问")) as HTMLElement | undefined;
    const modal = rectOf('[data-testid="settings-modal"]');
    const passRect = passButton ? passButton.getBoundingClientRect() : null;
    const sendRect = sendButton ? sendButton.getBoundingClientRect() : null;

    return {
      documentScrollWidth: document.documentElement.scrollWidth,
      documentClientWidth: document.documentElement.clientWidth,
      modal,
      center: rectOf('[data-testid="llm-interactive-center"]'),
      messages: rectOf('[data-testid="llm-interactive-messages"]'),
      composer: rectOf('[data-testid="llm-interactive-composer"]'),
      logHost: rectOf('[data-testid="llm-test-panel-host"]'),
      passButton: passRect ? { bottom: Math.round(passRect.bottom) } : null,
      sendButton: sendRect ? { bottom: Math.round(sendRect.bottom) } : null,
      textVisible: {
        answer: document.body.textContent?.includes("建议从四层审计") ?? false,
        thinking: document.body.textContent?.includes("实时思考过程") ?? false,
        tags: document.body.textContent?.includes("流式标签解析") ?? false,
        logPanel: document.body.textContent?.includes("交互式面试日志") ?? false,
      },
    };
  });

  expect(metrics.documentScrollWidth, "LLM deep layout should not create document horizontal overflow").toBeLessThanOrEqual(metrics.documentClientWidth + 4);
  expect(metrics.modal, "settings modal should be measurable").not.toBeNull();
  expect(metrics.center?.bottom, "conversation panel should stay above modal footer").toBeLessThanOrEqual((metrics.modal?.bottom || 0) - 48);
  expect(metrics.logHost?.right, "test log panel should stay inside modal").toBeLessThanOrEqual((metrics.modal?.right || 0) + 2);
  expect(metrics.messages?.height, "conversation scroll area should remain usable").toBeGreaterThanOrEqual(140);
  expect(metrics.messages?.scrollWidth, "conversation scroll area should not overflow horizontally").toBeLessThanOrEqual((metrics.messages?.clientWidth || 0) + 4);
  expect(metrics.composer?.bottom, "quick question composer should remain visible").toBeLessThanOrEqual((metrics.modal?.bottom || 0) - 48);
  expect(metrics.passButton?.bottom, "answer evaluation button should remain visible").toBeLessThanOrEqual((metrics.modal?.bottom || 0) - 48);
  expect(metrics.sendButton?.bottom, "send follow-up button should remain visible").toBeLessThanOrEqual((metrics.modal?.bottom || 0) - 48);
  expect(metrics.textVisible, "streaming, conversation, and log text should all be rendered").toEqual({
    answer: true,
    thinking: true,
    tags: true,
    logPanel: true,
  });

  await attachScreenshot(window, testInfo, "llm-deep-layout-contained");
});
