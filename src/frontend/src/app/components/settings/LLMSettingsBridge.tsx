/**
 * LLMSettingsBridge
 *
 * Bridge component that connects the LLM settings tab to the settings modal.
 * This is a thin wrapper around the LLMSettingsTab component that provides
 * the necessary props and callbacks.
 */

import { lazy, Suspense, useState, useCallback, useEffect, useRef } from 'react';
import { Loader2 } from 'lucide-react';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import type {
  SimpleProvider,
  ProviderConfig,
  LLMStatus,
  RoleConfig,
} from '@/app/components/llm/types';
import { isCLIProviderType } from '@/app/components/llm/types';
import type { TestEvent, TestResult } from '@/app/components/llm/test/types';
import type {
  InteractiveInterviewAnswer,
  InteractiveInterviewReport,
} from '@/app/components/llm/interview/InteractiveInterviewHall';
import type { RoleId } from '@/app/components/llm/state';

// Lazy load the heavy LLMSettingsTab component
const LLMSettingsTab = lazy(() =>
  import('@/app/components/llm/LLMSettingsTab').then((module) => ({ default: module.LLMSettingsTab }))
);

interface LLMSettingsBridgeProps {
  /** Callback when LLM status changes */
  onLlmStatusChange?: (status: LLMStatus | null) => void;
}

interface LlmConfig {
  schema_version: number;
  providers: Record<string, ProviderConfig>;
  roles: Record<string, RoleConfig>;
  policies?: {
    required_ready_roles?: string[];
    test_required_suites?: string[];
    role_requirements?: Record<string, {
      requires_thinking?: boolean;
      min_confidence?: number;
      error_message?: string;
    }>;
  };
  visual_layout?: Record<string, unknown>;
  visual_node_states?: Record<string, unknown>;
  visual_viewport?: Record<string, unknown>;
}

/**
 * Build test result from API response
 */
function buildTestResult(report: Record<string, unknown>): TestResult {
  let final = report?.final as Record<string, unknown> | undefined;

  if (!final && typeof report?.ready === 'boolean') {
    final = report;
  }

  const runId = typeof report?.test_run_id === 'string' ? report.test_run_id : undefined;
  const ready = typeof final?.ready === 'boolean' ? final.ready : undefined;
  const grade = typeof final?.grade === 'string' ? final.grade : undefined;

  return {
    report,
    runId,
    ready,
    grade,
  };
}

/**
 * LLM Settings Bridge Component
 */
export function LLMSettingsBridge({ onLlmStatusChange }: LLMSettingsBridgeProps) {
  const [llmConfig, setLLMConfig] = useState<LlmConfig | null>(null);
  const [llmStatus, setLLMStatus] = useState<LLMStatus | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [deletingProviders, setDeletingProviders] = useState<Record<string, boolean>>({});

  const llmConfigRef = useRef<LlmConfig | null>(null);
  const llmSavePendingRef = useRef<LlmConfig | null>(null);
  const llmSaveQueueRef = useRef<Promise<boolean>>(Promise.resolve(true));

  // Load LLM config
  const loadLLMConfig = useCallback(async () => {
    setLlmLoading(true);
    setLlmError(null);
    try {
      const res = await apiFetch('/v2/llm/config');
      if (!res.ok) {
        throw new Error('读取 LLM 配置失败');
      }
      const data = (await res.json()) as LlmConfig;
      setLLMConfig(data);
      llmConfigRef.current = data;
    } catch (err) {
      setLlmError(err instanceof Error ? err.message : '读取 LLM 配置失败');
    } finally {
      setLlmLoading(false);
    }
  }, []);

  // Load LLM status
  const loadLLMStatus = useCallback(async () => {
    try {
      const res = await apiFetch('/v2/llm/status');
      if (!res.ok) {
        throw new Error('读取 LLM 状态失败');
      }
      const data = (await res.json()) as LLMStatus;
      setLLMStatus(data);
      onLlmStatusChange?.(data);
    } catch {
      setLLMStatus(null);
      onLlmStatusChange?.(null);
    }
  }, [onLlmStatusChange]);

  // Initial load
  useEffect(() => {
    loadLLMConfig();
    loadLLMStatus();
  }, [loadLLMConfig, loadLLMStatus]);

  // Queue LLM save
  const queueLlmSave = useCallback(async (nextConfig: LlmConfig): Promise<boolean> => {
    llmSavePendingRef.current = nextConfig;
    const run = async (): Promise<boolean> => {
      if (!llmSavePendingRef.current) return true;
      setLlmSaving(true);
      setLlmError(null);
      let success = true;
      while (llmSavePendingRef.current) {
        const configToSave = llmSavePendingRef.current;
        llmSavePendingRef.current = null;
        try {
          const res = await apiFetch('/v2/llm/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configToSave),
          });
          if (!res.ok) {
            throw new Error('保存 LLM 配置失败');
          }
          const data = (await res.json()) as LlmConfig;
          setLLMConfig(data);
          llmConfigRef.current = data;
          await loadLLMStatus();
        } catch (err) {
          setLlmError(err instanceof Error ? err.message : '保存 LLM 配置失败');
          success = false;
          llmSavePendingRef.current = null;
          break;
        }
      }
      setLlmSaving(false);
      return success;
    };
    const runPromise = llmSaveQueueRef.current.then(run, run);
    llmSaveQueueRef.current = runPromise;
    return runPromise;
  }, [loadLLMStatus]);

  // Apply config mutation
  const applyLLMConfigMutation = useCallback(
    async (mutator: (current: LlmConfig) => LlmConfig) => {
      const current = llmConfigRef.current;
      if (!current) return null;
      const nextConfig = mutator(current);
      setLLMConfig(nextConfig);
      llmConfigRef.current = nextConfig;
      return nextConfig;
    },
    []
  );

  // Save config handler
  const handleSaveConfig = useCallback(
    async (config?: LlmConfig): Promise<boolean> => {
      const target = config || llmConfigRef.current;
      if (!target) return true;
      return queueLlmSave(target);
    },
    [queueLlmSave]
  );

  // Add provider
  const handleAddProvider = useCallback(
    async (providerId: string, provider: ProviderConfig) => {
      await applyLLMConfigMutation((current) => ({
        ...current,
        providers: {
          ...(current.providers || {}),
          [providerId]: provider,
        },
      }));
    },
    [applyLLMConfigMutation]
  );

  // Update provider
  const handleUpdateProvider = useCallback(
    async (providerId: string, updates: Partial<ProviderConfig>) => {
      await applyLLMConfigMutation((current) => ({
        ...current,
        providers: {
          ...(current.providers || {}),
          [providerId]: {
            ...(current.providers?.[providerId] || {}),
            ...updates,
          },
        },
      }));
    },
    [applyLLMConfigMutation]
  );

  // Delete provider
  const handleDeleteProvider = useCallback(
    async (providerId: string) => {
      setDeletingProviders((prev) => ({ ...prev, [providerId]: true }));
      try {
        await applyLLMConfigMutation((current) => {
          const nextProviders = { ...(current.providers || {}) };
          delete nextProviders[providerId];
          const nextRoles = { ...(current.roles || {}) };
          Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
            if (roleCfg?.provider_id === providerId) {
              nextRoles[roleId] = { ...roleCfg, provider_id: '', model: '' };
            }
          });
          return { ...current, providers: nextProviders, roles: nextRoles };
        });
      } finally {
        setDeletingProviders((prev) => {
          const next = { ...prev };
          delete next[providerId];
          return next;
        });
      }
    },
    [applyLLMConfigMutation]
  );

  // Update config
  const handleUpdateConfig = useCallback(
    (config: LlmConfig) => {
      setLLMConfig(config);
      llmConfigRef.current = config;
    },
    []
  );

  // Test provider
  const handleTestProvider = useCallback(
    async (provider: SimpleProvider, onEvent?: (event: TestEvent) => void): Promise<TestResult | null> => {
      try {
        const res = await apiFetch('/v2/llm/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider_id: provider.id,
            provider_type: provider.kind,
            model: provider.modelId,
          }),
        });

        if (!res.ok) {
          throw new Error('测试请求失败');
        }

        const report = (await res.json()) as Record<string, unknown>;
        return buildTestResult(report);
      } catch (err) {
        devLogger.error('Provider test failed:', err);
        return null;
      }
    },
    []
  );

  // Run interview
  const handleRunInterview = useCallback(
    async (
      role: RoleId,
      providerId: string,
      model: string,
      onEvent?: (event: TestEvent) => void
    ): Promise<Record<string, unknown> | null> => {
      try {
        const res = await apiFetch('/llm/interview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role, provider_id: providerId, model }),
        });

        if (!res.ok) {
          throw new Error('面试请求失败');
        }

        return (await res.json()) as Record<string, unknown>;
      } catch (err) {
        devLogger.error('Interview failed:', err);
        return null;
      }
    },
    []
  );

  // Run connectivity test
  const handleRunConnectivityTest = useCallback(
    async (
      role: RoleId,
      providerId: string,
      model: string
    ): Promise<Record<string, unknown> | null> => {
      try {
        const res = await apiFetch('/llm/connectivity', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role, provider_id: providerId, model }),
        });

        if (!res.ok) {
          throw new Error('连通性测试失败');
        }

        return (await res.json()) as Record<string, unknown>;
      } catch (err) {
        devLogger.error('Connectivity test failed:', err);
        return null;
      }
    },
    []
  );

  // Ask interactive interview
  const handleAskInteractiveInterview = useCallback(
    async (payload: {
      roleId: RoleId;
      providerId: string;
      model: string;
      question: string;
      expectedCriteria?: string[];
      expectsThinking?: boolean;
      sessionId?: string | null;
      context?: Array<{ question: string; answer: string }>;
    }): Promise<InteractiveInterviewAnswer | null> => {
      try {
        const res = await apiFetch('/v2/llm/interview/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        if (!res.ok) {
          throw new Error('交互面试请求失败');
        }

        return (await res.json()) as InteractiveInterviewAnswer;
      } catch (err) {
        devLogger.error('Interactive interview failed:', err);
        return null;
      }
    },
    []
  );

  // Save interactive interview
  const handleSaveInteractiveInterview = useCallback(
    async (payload: {
      roleId: RoleId;
      providerId: string;
      model: string | null;
      report: InteractiveInterviewReport;
    }): Promise<{ saved: boolean; report_path?: string } | null> => {
      try {
        const res = await apiFetch('/v2/llm/interview/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        if (!res.ok) {
          throw new Error('保存面试报告失败');
        }

        return (await res.json()) as { saved: boolean; report_path?: string };
      } catch (err) {
        devLogger.error('Save interview failed:', err);
        return null;
      }
    },
    []
  );

  // Resolve provider env overrides
  const handleResolveEnvOverrides = useCallback(
    async (providerId: string): Promise<Record<string, string> | null> => {
      const cfg = llmConfigRef.current?.providers?.[providerId];
      if (!cfg || !isCLIProviderType(String(cfg.type || ''))) {
        return null;
      }

      const env = cfg.env && typeof cfg.env === 'object' ? cfg.env : {};
      const resolved: Record<string, string> = {};
      for (const [key, value] of Object.entries(env)) {
        if (value === undefined || value === null) {
          continue;
        }
        const raw = String(value).trim();
        const match = raw.match(/^\$?\{?keychain:([^}]+)\}?$/i);
        if (match && window.polaris?.secrets?.get) {
          try {
            const result = await window.polaris.secrets.get(match[1]);
            if (result?.ok && result.value) {
              resolved[key] = String(result.value);
            }
          } catch {
            // Keep env resolution best-effort; the test request still carries non-secret overrides.
          }
        } else {
          resolved[key] = raw;
        }
      }

      return Object.keys(resolved).length > 0 ? resolved : null;
    },
    []
  );

  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-12">
          <div className="flex items-center gap-2 text-text-muted">
            <Loader2 className="size-4 animate-spin" />
            <span className="text-sm">正在载入 LLM 配置...</span>
          </div>
        </div>
      }
    >
      <LLMSettingsTab
        llmConfig={llmConfig}
        llmStatus={llmStatus}
        llmLoading={llmLoading}
        llmSaving={llmSaving}
        llmError={llmError}
        deletingProviders={deletingProviders}
        onSaveConfig={handleSaveConfig}
        onRunInterview={handleRunInterview}
        onRunConnectivityTest={handleRunConnectivityTest}
        onAskInteractiveInterview={handleAskInteractiveInterview}
        onSaveInteractiveInterview={handleSaveInteractiveInterview}
        resolveProviderEnvOverrides={handleResolveEnvOverrides}
        onAddProvider={handleAddProvider}
        onUpdateProvider={handleUpdateProvider}
        onDeleteProvider={handleDeleteProvider}
        onUpdateConfig={handleUpdateConfig}
        onTestProvider={handleTestProvider}
      />
    </Suspense>
  );
}
