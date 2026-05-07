/**
 * LLM Store - Zustand 状态管理
 * 集中管理 LLM 配置相关状态
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type {
  LLMConfig,
  LLMStatus,
  ProviderConfig,
  RoleConfig,
} from '@/app/components/llm/types';
import { apiFetch } from '@/api';

// ============================================================================
// Types
// ============================================================================

export interface LLMState {
  // LLM配置状态
  llmConfig: LLMConfig | null;
  llmStatus: LLMStatus | null;
  llmLoading: boolean;
  llmSaving: boolean;
  llmError: string | null;

  // Provider 相关
  providerModels: Record<string, { supported: boolean; models: string[] }>;
  providerKeyDrafts: Record<string, string>;
  providerKeyStatus: Record<string, string>;
  deletingProviders: Record<string, boolean>;
}

export interface LLMActions {
  // LLM状态设置
  setLlmConfig: (config: LLMConfig | null) => void;
  setLlmStatus: (status: LLMStatus | null) => void;
  setLlmLoading: (loading: boolean) => void;
  setLlmSaving: (saving: boolean) => void;
  setLlmError: (error: string | null) => void;
  setProviderKeyDraft: (providerId: string, draft: string) => void;
  setProviderKeyStatus: (providerId: string, status: string) => void;
  setDeletingProvider: (providerId: string, deleting: boolean) => void;

  // LLM配置操作
  updateRole: (role: string, updates: Partial<RoleConfig>) => void;
  updateProvider: (providerId: string, updates: Partial<ProviderConfig>) => void;
  addProvider: (providerId: string, provider: ProviderConfig) => void;
  deleteProvider: (providerId: string) => void;

  // 异步操作
  loadLLMConfig: () => Promise<void>;
  loadLLMStatus: (onChange?: (status: LLMStatus | null) => void) => Promise<void>;
  saveLLMConfig: (config?: LLMConfig) => Promise<boolean>;
  saveProviderKey: (providerId: string) => Promise<void>;
  loadProviderModels: (providerId: string) => Promise<void>;
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
    if (result?.ok && result.value) {
      return String(result.value);
    }
  } catch {
    // ignore
  }
  return null;
};

const refreshProviderKeyStatus = async (providers: Record<string, ProviderConfig>) => {
  if (!window.polaris?.secrets?.get) return;
  const status: Record<string, string> = {};
  for (const [providerId, cfg] of Object.entries(providers)) {
    if (cfg.type !== 'openai_compat' && cfg.type !== 'anthropic_compat' && cfg.type !== 'codex_sdk') continue;
    const keyRef = cfg.api_key_ref || `keychain:llm:${providerId}`;
    const keyName = keyRef.startsWith('keychain:') ? keyRef.slice('keychain:'.length) : keyRef;
    try {
      const result = await window.polaris.secrets.get(keyName);
      if (result?.ok && result.value) {
        const value = String(result.value);
        const mask = value.length > 8 ? `${value.slice(0, 3)}****${value.slice(-4)}` : 'stored';
        status[providerId] = mask;
      }
    } catch {
      // ignore
    }
  }
  return status;
};

// ============================================================================
// Store Creation
// ============================================================================

export const useLLMStore = create<LLMState & LLMActions>()(
  immer((set, get) => {
    // 用于保存的refs
    let llmConfigRef: LLMConfig | null = null;
    let llmSavePendingRef: LLMConfig | null = null;
    let llmSaveQueueRef: Promise<boolean> = Promise.resolve(true);

    return {
      // ============ 初始状态 ============
      llmConfig: null,
      llmStatus: null,
      llmLoading: false,
      llmSaving: false,
      llmError: null,
      providerModels: {},
      providerKeyDrafts: {},
      providerKeyStatus: {},
      deletingProviders: {},

      // ============ LLM状态设置 ============
      setLlmConfig: (config) => {
        llmConfigRef = config;
        set((state) => { state.llmConfig = config; });
      },
      setLlmStatus: (status) => set((state) => { state.llmStatus = status; }),
      setLlmLoading: (loading) => set((state) => { state.llmLoading = loading; }),
      setLlmSaving: (saving) => set((state) => { state.llmSaving = saving; }),
      setLlmError: (error) => set((state) => { state.llmError = error; }),
      setProviderKeyDraft: (providerId, draft) => set((state) => { state.providerKeyDrafts[providerId] = draft; }),
      setProviderKeyStatus: (providerId, status) => set((state) => { state.providerKeyStatus[providerId] = status; }),
      setDeletingProvider: (providerId, deleting) => set((state) => { state.deletingProviders[providerId] = deleting; }),

      // ============ LLM配置操作 ============
      updateRole: (role, updates) => {
        set((state) => {
          if (!state.llmConfig) return;
          state.llmConfig.roles = {
            ...state.llmConfig.roles,
            [role]: { ...state.llmConfig.roles[role], ...updates },
          };
          llmConfigRef = state.llmConfig;
        });
      },

      updateProvider: (providerId, updates) => {
        set((state) => {
          if (!state.llmConfig) return;
          const prevProvider = state.llmConfig.providers?.[providerId] || {};
          const prevModel =
            typeof prevProvider.model === 'string'
              ? prevProvider.model
              : typeof prevProvider.model_id === 'string'
                ? prevProvider.model_id
                : typeof prevProvider.default_model === 'string'
                  ? prevProvider.default_model
                  : '';
          const nextProvider = { ...prevProvider, ...updates };
          const nextModel =
            typeof nextProvider.model === 'string'
              ? nextProvider.model
              : typeof nextProvider.model_id === 'string'
                ? nextProvider.model_id
                : typeof nextProvider.default_model === 'string'
                  ? nextProvider.default_model
                  : '';

          let nextRoles = state.llmConfig.roles || {};
          if (nextModel && nextModel !== prevModel) {
            nextRoles = { ...nextRoles };
            Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
              if (!roleCfg || typeof roleCfg !== 'object') return;
              const roleConfig = roleCfg as { provider_id?: string; model?: string };
              if (roleConfig.provider_id !== providerId) return;
              const roleModel = typeof roleConfig.model === 'string' ? roleConfig.model : '';
              if (!roleModel || roleModel === prevModel) {
                nextRoles[roleId] = { ...roleConfig, model: nextModel };
              }
            });
          }

          state.llmConfig.providers = {
            ...state.llmConfig.providers,
            [providerId]: nextProvider,
          };
          state.llmConfig.roles = nextRoles;
          llmConfigRef = state.llmConfig;
        });
      },

      addProvider: (providerId, provider) => {
        set((state) => {
          if (!state.llmConfig) return;
          state.llmConfig.providers = {
            ...state.llmConfig.providers,
            [providerId]: provider,
          };
          llmConfigRef = state.llmConfig;
        });
      },

      deleteProvider: (providerId) => {
        set((state) => {
          if (!state.llmConfig) return;
          const nextProviders = { ...state.llmConfig.providers };
          delete nextProviders[providerId];
          const nextRoles = { ...state.llmConfig.roles };
          Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
            const roleConfig = roleCfg as { provider_id?: string };
            if (roleConfig?.provider_id === providerId) {
              nextRoles[roleId] = { ...roleConfig, provider_id: '', model: '' };
            }
          });
          state.llmConfig.providers = nextProviders;
          state.llmConfig.roles = nextRoles;
          llmConfigRef = state.llmConfig;
        });
      },

      // ============ 异步操作 ============
      loadLLMConfig: async () => {
        set((state) => { state.llmLoading = true; state.llmError = null; });
        try {
          const res = await apiFetch('/v2/llm/config');
          if (!res.ok) throw new Error('读取 LLM 配置失败');
          const data = await res.json() as LLMConfig;
          llmConfigRef = data;
          set((state) => {
            state.llmConfig = data;
            state.llmLoading = false;
          });
          const status = await refreshProviderKeyStatus(data.providers || {});
          if (status) {
            set((s) => { s.providerKeyStatus = status; });
          }
        } catch (err) {
          set((state) => {
            state.llmError = err instanceof Error ? err.message : '读取 LLM 配置失败';
            state.llmLoading = false;
          });
        }
      },

      loadLLMStatus: async (onChange) => {
        try {
          const res = await apiFetch('/v2/llm/status');
          if (!res.ok) throw new Error('读取 LLM 状态失败');
          const data = await res.json() as LLMStatus;
          set((state) => { state.llmStatus = data; });
          onChange?.(data);
        } catch {
          set((state) => { state.llmStatus = null; });
          onChange?.(null);
        }
      },

      saveLLMConfig: async (config) => {
        const target = config || llmConfigRef;
        if (!target) return true;

        llmSavePendingRef = target;

        const run = async (): Promise<boolean> => {
          if (!llmSavePendingRef) return true;
          set((state) => { state.llmSaving = true; state.llmError = null; });
          let success = true;

          while (llmSavePendingRef) {
            const configToSave = llmSavePendingRef;
            llmSavePendingRef = null;
            try {
              const res = await apiFetch('/v2/llm/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(configToSave),
              });
              if (!res.ok) throw new Error('保存 LLM 配置失败');
              const data = await res.json() as LLMConfig;
              llmConfigRef = data;
              set((state) => { state.llmConfig = data; });
              const status = await refreshProviderKeyStatus(data.providers || {});
              if (status) {
                set((s) => { s.providerKeyStatus = status; });
              }
              await get().loadLLMStatus();
            } catch (err) {
              set((state) => {
                state.llmError = err instanceof Error ? err.message : '保存 LLM 配置失败';
              });
              success = false;
              llmSavePendingRef = null;
              break;
            }
          }
          set((state) => { state.llmSaving = false; });
          return success;
        };

        const runPromise = llmSaveQueueRef.then(run, run);
        llmSaveQueueRef = runPromise;
        return runPromise;
      },

      saveProviderKey: async (providerId) => {
        const key = get().providerKeyDrafts[providerId];
        if (!key || !window.polaris?.secrets?.set) return;
        const ref = `keychain:llm:${providerId}`;
        const keyName = ref.slice('keychain:'.length);
        const result = await window.polaris.secrets.set(keyName, key);
        if (result?.ok) {
          set((state) => {
            if (!state.llmConfig) return;
            state.llmConfig.providers = {
              ...state.llmConfig.providers,
              [providerId]: {
                ...state.llmConfig.providers?.[providerId],
                api_key_ref: ref,
              },
            };
            state.providerKeyDrafts[providerId] = '';
            state.providerKeyStatus[providerId] = `${key.slice(0, 3)}****${key.slice(-4)}`;
            llmConfigRef = state.llmConfig;
          });
        }
      },

      loadProviderModels: async (providerId) => {
        const state = get();
        if (!state.llmConfig || !providerId) return;

        const providerCfg = state.llmConfig.providers?.[providerId];
        if (!providerCfg) return;

        const apiKey = await resolveApiKey(providerId, providerCfg);
        const res = await apiFetch(`/v2/llm/providers/${providerId}/models`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: apiKey }),
        });

        if (!res.ok) return;
        const payload = await res.json() as { supported?: boolean; models?: Array<string | { id?: string }> };
        const rawModels = Array.isArray(payload.models) ? payload.models : [];
        const models = rawModels
          .map((model) => (typeof model === 'string' ? model : model?.id))
          .filter((modelId): modelId is string => typeof modelId === 'string' && modelId.length > 0);

        set((s) => { s.providerModels[providerId] = { supported: !!payload.supported, models }; });
      },
    };
  })
);

// ============================================================================
// Selector Hooks
// ============================================================================

/** LLM 设置（配置和状态） */
export const useLlmSettings = () => useLLMStore((state) => ({
  llmConfig: state.llmConfig,
  llmStatus: state.llmStatus,
  llmLoading: state.llmLoading,
  llmSaving: state.llmSaving,
  llmError: state.llmError,
  providerModels: state.providerModels,
  providerKeyDrafts: state.providerKeyDrafts,
  providerKeyStatus: state.providerKeyStatus,
  deletingProviders: state.deletingProviders,
}));

/** LLM 配置（只读） */
export const useLlmConfig = () => useLLMStore((state) => state.llmConfig);

/** LLM 状态（只读） */
export const useLlmStatus = () => useLLMStore((state) => state.llmStatus);

/** Provider 列表 */
export const useProviders = () => useLLMStore((state) => state.llmConfig?.providers || {});

/** Provider 模型列表 */
export const useProviderModels = (providerId: string) =>
  useLLMStore((state) => state.providerModels[providerId]);

/** Provider Key 状态 */
export const useProviderKeyStatus = (providerId: string) =>
  useLLMStore((state) => state.providerKeyStatus[providerId]);
