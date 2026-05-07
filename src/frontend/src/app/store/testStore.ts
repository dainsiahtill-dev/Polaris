/**
 * Test Store - Zustand 状态管理
 * 集中管理测试执行和报告相关状态
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type { TestEvent, TestResult } from '@/app/components/llm/test/types';
import { apiFetch } from '@/api';
import type { LLMConfig, ProviderConfig, SimpleProvider } from '@/app/components/llm/types';
import { isCLIProviderType } from '@/app/components/llm/types';
import { runStreamingTest } from '@/app/components/llm/test/streamingTest';
import { resolveProviderAwareRoleModel } from '@/app/components/llm/utils/providerModelResolver';

// ============================================================================
// Types
// ============================================================================

export interface TestState {
  // 测试状态
  reportDrawer: { open: boolean; data: unknown | null };
  testSuites: { connectivity: boolean; response: boolean; qualification: boolean };
  testLevel: 'quick' | 'full';
  runAllBusy: boolean;
  llmTesting: Record<string, boolean>;

  // 错误状态
  llmError: string | null;
}

export interface TestActions {
  // 测试状态设置
  setReportDrawer: (drawer: { open: boolean; data: unknown | null }) => void;
  setTestSuites: (suites: Partial<TestState['testSuites']>) => void;
  setTestLevel: (level: 'quick' | 'full') => void;
  setRunAllBusy: (busy: boolean) => void;
  setLlmTesting: (role: string, testing: boolean) => void;
  setLlmError: (error: string | null) => void;

  // 测试执行
  runProviderTestStreaming: (
    provider: SimpleProvider,
    llmConfig: LLMConfig | null,
    onEvent?: (event: TestEvent) => void
  ) => Promise<TestResult | null>;
  runLlmTest: (
    role: string,
    llmConfig: LLMConfig | null,
    level?: 'quick' | 'full',
    suites?: string[],
    showReport?: boolean,
    overrides?: { providerId?: string; model?: string }
  ) => Promise<Record<string, unknown> | null>;
  runConnectivityTest: (
    role: string,
    providerId: string,
    model: string,
    llmConfig: LLMConfig | null
  ) => Promise<Record<string, unknown> | null>;
  runAllTests: (
    llmConfig: LLMConfig | null,
    testSuites: TestState['testSuites'],
    testLevel: TestState['testLevel']
  ) => Promise<void>;

  // 取消操作
  cancelTest: () => void;
}

// ============================================================================
// Helper Functions
// ============================================================================

const resolveApiKey = async (providerId: string, cfg: ProviderConfig): Promise<string | null> => {
  if (cfg.type !== 'openai_compat' && cfg.type !== 'anthropic_compat' && cfg.type !== 'codex_sdk') return null;
  if (!window.polaris?.secrets?.get) return null;
  const keyRef = cfg.api_key_ref || `keychain:llm:${providerId}`;
  const keyName = keyRef.startsWith('keychain:') ? keyRef.slice('keychain:'.length) : keyRef;
  try {
    const result = await window.polaris.secrets.get(keyName);
    if (result?.ok && result.value) return String(result.value);
  } catch { /* ignore */ }
  return null;
};

const resolveEnvOverrides = async (providerId: string, cfg: ProviderConfig) => {
  if (!isCLIProviderType(String(cfg.type || ''))) return null;
  const env = cfg.env && typeof cfg.env === 'object' ? cfg.env : {};
  const resolved: Record<string, string> = {};
  for (const [key, value] of Object.entries(env)) {
    if (value === undefined || value === null) continue;
    const raw = String(value).trim();
    const match = raw.match(/^\$?\{?keychain:([^}]+)\}?$/i);
    if (match && window.polaris?.secrets?.get) {
      try {
        const result = await window.polaris.secrets.get(match[1]);
        if (result?.ok && result.value) resolved[key] = String(result.value);
      } catch { /* ignore */ }
    } else {
      resolved[key] = raw;
    }
  }
  return { env: resolved };
};

const resolveModel = (providerCfg: ProviderConfig | undefined): string => {
  if (!providerCfg) return '';
  return typeof providerCfg.model === 'string' ? providerCfg.model :
    typeof providerCfg.model_id === 'string' ? providerCfg.model_id :
      typeof providerCfg.default_model === 'string' ? providerCfg.default_model : '';
};

// ============================================================================
// Store Creation
// ============================================================================

export const useTestStore = create<TestState & TestActions>()(
  immer((set, get) => {
    let testAbortRef: AbortController | null = null;

    return {
      // ============ 初始状态 ============
      reportDrawer: { open: false, data: null },
      testSuites: { connectivity: true, response: true, qualification: false },
      testLevel: 'quick',
      runAllBusy: false,
      llmTesting: {},
      llmError: null,

      // ============ 测试状态设置 ============
      setReportDrawer: (drawer) => set((state) => { state.reportDrawer = drawer; }),
      setTestSuites: (suites) => set((state) => { state.testSuites = { ...state.testSuites, ...suites }; }),
      setTestLevel: (level) => set((state) => { state.testLevel = level; }),
      setRunAllBusy: (busy) => set((state) => { state.runAllBusy = busy; }),
      setLlmTesting: (role, testing) => set((state) => { state.llmTesting[role] = testing; }),
      setLlmError: (error) => set((state) => { state.llmError = error; }),

      // ============ 测试执行 ============
      runProviderTestStreaming: async (provider, llmConfig, onEvent) => {
        if (!llmConfig) return null;
        const providerId = provider.id;
        const providerCfg = llmConfig.providers?.[providerId];
        if (!providerCfg) {
          onEvent?.({ type: 'error', timestamp: new Date().toISOString(), content: `未找到提供商 "${providerId}"` });
          return null;
        }
        const testModel = provider.modelId || resolveModel(providerCfg);
        const controller = new AbortController();
        testAbortRef = controller;
        try {
          const report = await runStreamingTest({
            role: 'connectivity',
            providerId,
            model: testModel,
            suites: ['connectivity'],
            testLevel: 'quick',
            evaluationMode: 'provider',
            apiKey: await resolveApiKey(providerId, providerCfg),
            envOverrides: (await resolveEnvOverrides(providerId, providerCfg))?.env,
            onEvent,
            signal: controller.signal,
          });
          if (!report) return null;
          return {
            ready: (report.final as { ready?: boolean })?.ready ?? false,
            grade: (report.final as { grade?: string })?.grade || 'FAIL',
            report,
            suites: Object.entries(report.suites || {}).map(([name, suiteResult]) => ({
              name,
              ok: (suiteResult as { ok?: boolean })?.ok ?? false,
            })),
          };
        } catch (err) {
          if (err instanceof Error && err.name !== 'AbortError') {
            onEvent?.({ type: 'error', timestamp: new Date().toISOString(), content: err.message });
            set((s) => { s.llmError = err.message; });
          }
          return null;
        } finally {
          if (testAbortRef === controller) testAbortRef = null;
        }
      },

      runLlmTest: async (role, llmConfig, level = 'quick', suites, showReport = true, overrides) => {
        if (!llmConfig) return null;
        const roleCfg = llmConfig.roles?.[role];
        const providerIdRaw = overrides?.providerId || roleCfg?.provider_id;
        const providerId = typeof providerIdRaw === 'string' ? providerIdRaw.trim() : '';
        if (!providerId) return null;
        const providerCfg = llmConfig.providers?.[providerId];
        const model = resolveProviderAwareRoleModel(roleCfg, providerId, providerCfg, overrides?.model);
        if (!model) return null;
        set((s) => { s.llmTesting[role] = true; });
        try {
          const res = await apiFetch('/v2/llm/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              role,
              provider_id: providerId,
              model,
              suites: suites || llmConfig.policies?.test_required_suites,
              test_level: level,
              api_key: providerCfg ? await resolveApiKey(providerId, providerCfg) : null,
              env_overrides: providerCfg ? (await resolveEnvOverrides(providerId, providerCfg))?.env : undefined,
            }),
          });
          if (!res.ok) throw new Error('LLM test failed');
          const report = await res.json();
          if (showReport) set((s) => { s.reportDrawer = { open: true, data: report }; });
          return report as Record<string, unknown>;
        } catch (err) {
          set((s) => { s.llmError = err instanceof Error ? err.message : 'LLM test failed'; });
          return null;
        } finally {
          set((s) => { s.llmTesting[role] = false; });
        }
      },

      runConnectivityTest: async (role, providerId, model, llmConfig) => {
        if (!llmConfig) return null;
        const providerCfg = llmConfig.providers?.[providerId];
        const controller = new AbortController();
        testAbortRef = controller;
        try {
          return await runStreamingTest({
            role: 'connectivity',
            providerId,
            model,
            suites: ['connectivity'],
            testLevel: 'quick',
            evaluationMode: 'provider',
            apiKey: providerCfg ? await resolveApiKey(providerId, providerCfg) : null,
            envOverrides: providerCfg ? (await resolveEnvOverrides(providerId, providerCfg))?.env : undefined,
            signal: controller.signal,
          }) || null;
        } catch (err) {
          if (err instanceof Error && err.name !== 'AbortError') set((s) => { s.llmError = err.message; });
          return null;
        } finally {
          if (testAbortRef === controller) testAbortRef = null;
        }
      },

      runAllTests: async (llmConfig, testSuites, testLevel) => {
        if (!llmConfig) return;
        set((s) => { s.runAllBusy = true; });
        const suites = Object.entries(testSuites).filter(([, enabled]) => enabled).map(([name]) => name);
        for (const role of Object.keys(llmConfig.roles || {})) {
          await get().runLlmTest(role, llmConfig, testLevel, suites, false);
        }
        set((s) => { s.runAllBusy = false; });
      },

      cancelTest: () => { testAbortRef?.abort(); testAbortRef = null; },
    };
  })
);

// ============================================================================
// Selector Hooks
// ============================================================================

export const useTestState = () => useTestStore((state) => ({
  reportDrawer: state.reportDrawer,
  testSuites: state.testSuites,
  testLevel: state.testLevel,
  runAllBusy: state.runAllBusy,
  llmTesting: state.llmTesting,
  llmError: state.llmError,
}));

export const useReportDrawer = () => useTestStore((state) => state.reportDrawer);
