/**
 * Provider Store - Zustand 状态管理
 * 替代 ProviderContext，统一管理 LLM Provider 相关状态
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  ProviderState,
  ProviderAction,
  ConnectivityResultStrict,
  ConnectivityStatus,
  TestStatus,
  ConfigView,
  DeepView,
  InterviewMode,
  ActiveTab,
  InterviewPanelState,
  TestPanelState,
} from '@/app/components/llm/state/providerReducer';
import type { RoleIdStrict, InterviewSuiteReportStrict } from '@/app/components/llm/types/strict';
import type { ProviderConfig, UnifiedLlmConfig } from '@/app/components/llm/types';

// ============================================================================
// Persistence Constants
// ============================================================================

const PROVIDER_STATUS_KEY = 'llm_provider_test_status';
const CONNECTIVITY_RESULTS_KEY = 'llm_connectivity_results';
const PROVIDER_STATUS_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
const CONNECTIVITY_TTL_MS = 5 * 60 * 1000; // 5 minutes

interface PersistedProviderStatus {
  status: 'success' | 'failed';
  timestamp: number;
  model?: string;
}

// ============================================================================
// Types
// ============================================================================

/** 连接方式 */
export type ConnectionMethodId = 'sdk' | 'api' | 'cli';

/** Serializable Provider State (Map/Set converted to Arrays/Records) */
export interface SerializableProviderState {
  // Selection
  selectedRole: RoleIdStrict;
  selectedProviderId: string | null;
  selectedMethod: ConnectionMethodId;

  // View
  activeTab: ActiveTab;
  configView: ConfigView;
  deepView: DeepView;
  interviewMode: InterviewMode;

  // Legacy edit
  editingProvider: string | null;
  expandedProviders: string[]; // Set -> Array

  // New edit
  editingProviderId: string | null;
  editFormState: Record<string, ProviderConfig>;
  pendingChanges: string[]; // Set -> Array
  savingProvider: string | null;

  // Test
  testPanel: TestPanelState;
  providerTestStatus: Record<string, ConnectivityStatus>;
  connectivityResults: Record<string, ConnectivityResultStrict>; // Map -> Record
  connectivityRunning: boolean;
  connectivityRunningKey: string | null;

  // Interview
  interviewPanel: InterviewPanelState;
  interviewRunning: boolean;
  interviewCancelled: boolean;

  // Config
  unifiedConfig: UnifiedLlmConfig | null;

  // Errors
  globalError: string | null;
  providerErrors: Record<string, string | undefined>;
}

export interface ProviderStoreState extends SerializableProviderState {}

export interface ProviderStoreActions {
  // Selection
  selectRole: (role: RoleIdStrict) => void;
  selectProvider: (id: string | null) => void;
  selectMethod: (method: ConnectionMethodId) => void;

  // View
  switchTab: (tab: ActiveTab) => void;
  setConfigView: (view: ConfigView) => void;
  setDeepView: (view: DeepView) => void;
  setInterviewMode: (mode: InterviewMode) => void;

  // Legacy edit
  startEditProvider: (id: string) => void;
  stopEditProvider: () => void;
  toggleExpandProvider: (id: string) => void;
  collapseAllProviders: () => void;

  // New edit
  startEdit: (providerId: string, initialConfig: ProviderConfig) => void;
  updateEditForm: (providerId: string, updates: Partial<ProviderConfig>) => void;
  saveEditStart: (providerId: string) => void;
  saveEditSuccess: (providerId: string) => void;
  saveEditFailure: (providerId: string, error: string) => void;
  cancelEdit: (providerId: string) => void;
  setProviderError: (providerId: string, error: string | null | undefined) => void;
  clearProviderError: (providerId: string) => void;

  // Test
  openTestPanel: (id: string, runConfig?: { suites?: string[]; role?: string; model?: string }) => void;
  closeTestPanel: () => void;
  startTest: (id: string, runConfig?: { suites?: string[]; role?: string; model?: string }) => void;
  completeTest: (id: string, success: boolean) => void;
  cancelTest: () => void;

  // Connectivity
  startConnectivityTest: (key: string) => void;
  completeConnectivityTest: (key: string, result: ConnectivityResultStrict) => void;
  clearConnectivityResult: (key: string) => void;

  // Interview
  openInterviewPanel: () => void;
  closeInterviewPanel: () => void;
  startInterview: () => void;
  completeInterview: (report: InterviewSuiteReportStrict) => void;
  failInterview: (error: string) => void;
  cancelInterview: () => void;

  // Error
  setError: (error: string | null) => void;
  clearError: () => void;

  // Config
  updateUnifiedConfig: (config: UnifiedLlmConfig) => void;

  // Persistence
  clearPersistedStatus: () => void;
  hydrateState: (state: Partial<SerializableProviderState>) => void;
}

// ============================================================================
// Persistence Helpers
// ============================================================================

function restoreProviderTestStatus(): Record<string, ConnectivityStatus> {
  if (typeof window === 'undefined') return {};

  try {
    const stored = localStorage.getItem(PROVIDER_STATUS_KEY);
    if (!stored) return {};

    const parsed: Record<string, PersistedProviderStatus> = JSON.parse(stored);
    const now = Date.now();
    const restored: Record<string, ConnectivityStatus> = {};

    Object.entries(parsed).forEach(([providerId, data]) => {
      if (now - data.timestamp <= PROVIDER_STATUS_TTL_MS) {
        restored[providerId] = data.status;
      }
    });

    return restored;
  } catch {
    return {};
  }
}

function restoreConnectivityResults(): Record<string, ConnectivityResultStrict> {
  if (typeof window === 'undefined') return {};

  try {
    const stored = localStorage.getItem(CONNECTIVITY_RESULTS_KEY);
    if (!stored) return {};

    const parsed: Record<string, ConnectivityResultStrict> = JSON.parse(stored);
    const now = Date.now();
    const restored: Record<string, ConnectivityResultStrict> = {};

    Object.entries(parsed).forEach(([key, result]) => {
      const timestamp = new Date(result.timestamp).getTime();
      if (now - timestamp <= CONNECTIVITY_TTL_MS) {
        restored[key] = result;
      }
    });

    return restored;
  } catch {
    return {};
  }
}

function persistProviderTestStatus(status: Record<string, ConnectivityStatus>): void {
  if (typeof window === 'undefined') return;

  try {
    const toPersist: Record<string, PersistedProviderStatus> = {};
    Object.entries(status).forEach(([providerId, connStatus]) => {
      if (connStatus === 'success' || connStatus === 'failed') {
        toPersist[providerId] = {
          status: connStatus,
          timestamp: Date.now(),
        };
      }
    });
    localStorage.setItem(PROVIDER_STATUS_KEY, JSON.stringify(toPersist));
  } catch {
    // ignore storage errors
  }
}

function persistConnectivityResults(results: Record<string, ConnectivityResultStrict>): void {
  if (typeof window === 'undefined') return;

  try {
    localStorage.setItem(CONNECTIVITY_RESULTS_KEY, JSON.stringify(results));
  } catch {
    // ignore storage errors
  }
}

// ============================================================================
// Initial State
// ============================================================================

const getInitialState = (): SerializableProviderState => ({
  selectedRole: 'pm',
  selectedProviderId: null,
  selectedMethod: 'sdk',

  activeTab: 'config',
  configView: 'list',
  deepView: 'hall',
  interviewMode: 'interactive',

  editingProvider: null,
  expandedProviders: [],

  editingProviderId: null,
  editFormState: {},
  pendingChanges: [],
  savingProvider: null,

  testPanel: {
    selectedProviderId: null,
    status: 'idle',
    cancelled: false,
  },
  providerTestStatus: restoreProviderTestStatus(),
  connectivityResults: restoreConnectivityResults(),
  connectivityRunning: false,
  connectivityRunningKey: null,

  interviewPanel: {
    open: false,
    status: 'idle',
    report: null,
    error: null,
  },
  interviewRunning: false,
  interviewCancelled: false,

  unifiedConfig: null,

  globalError: null,
  providerErrors: {},
});

// ============================================================================
// Store Creation
// ============================================================================

export const useProviderStore = create<ProviderStoreState & ProviderStoreActions>()(
  persist(
    (set, get) => ({
      ...getInitialState(),

      // ============ Selection ============
      selectRole: (role) => set({
        selectedRole: role,
        interviewPanel: {
          ...get().interviewPanel,
          error: null,
        },
      }),

      selectProvider: (id) => set({ selectedProviderId: id }),

      selectMethod: (method) => set({ selectedMethod: method }),

      // ============ View ============
      switchTab: (tab) => {
        const updates: Partial<SerializableProviderState> = { activeTab: tab };
        if (tab !== 'deepTest') {
          updates.interviewPanel = {
            open: false,
            status: 'idle',
            report: null,
            error: null,
          };
        }
        set(updates);
      },

      setConfigView: (view) => set({ configView: view }),

      setDeepView: (view) => set({ deepView: view }),

      setInterviewMode: (mode) => set({
        interviewMode: mode,
        deepView: mode === 'auto' ? 'hall' : get().deepView,
      }),

      // ============ Legacy Edit ============
      startEditProvider: (id) => set({ editingProvider: id }),

      stopEditProvider: () => set({ editingProvider: null }),

      toggleExpandProvider: (id) => set((state) => {
        const expanded = new Set(state.expandedProviders);
        if (expanded.has(id)) {
          expanded.delete(id);
        } else {
          expanded.add(id);
        }
        return { expandedProviders: Array.from(expanded) };
      }),

      collapseAllProviders: () => set({ expandedProviders: [] }),

      // ============ New Edit ============
      startEdit: (providerId, initialConfig) => set((state) => ({
        editingProviderId: providerId,
        editFormState: {
          ...state.editFormState,
          [providerId]: JSON.parse(JSON.stringify(initialConfig)),
        },
        pendingChanges: state.pendingChanges.filter((id) => id !== providerId),
        providerErrors: {
          ...state.providerErrors,
          [providerId]: undefined,
        },
      })),

      updateEditForm: (providerId, updates) => set((state) => {
        const currentForm = state.editFormState[providerId];
        if (!currentForm) return state;

        const updatedForm = { ...currentForm, ...updates };
        const hasChanges = JSON.stringify(currentForm) !== JSON.stringify(updatedForm);

        return {
          editFormState: {
            ...state.editFormState,
            [providerId]: updatedForm,
          },
          pendingChanges: hasChanges
            ? [...state.pendingChanges.filter((id) => id !== providerId), providerId]
            : state.pendingChanges.filter((id) => id !== providerId),
        };
      }),

      saveEditStart: (providerId) => set({ savingProvider: providerId }),

      saveEditSuccess: (providerId) => set((state) => {
        const newFormState = { ...state.editFormState };
        delete newFormState[providerId];
        return {
          savingProvider: null,
          editingProviderId: null,
          pendingChanges: state.pendingChanges.filter((id) => id !== providerId),
          editFormState: newFormState,
        };
      }),

      saveEditFailure: (providerId, error) => set((state) => ({
        savingProvider: null,
        providerErrors: {
          ...state.providerErrors,
          [providerId]: error,
        },
      })),

      cancelEdit: (providerId) => set((state) => {
        const newFormState = { ...state.editFormState };
        delete newFormState[providerId];
        const newErrors = { ...state.providerErrors };
        delete newErrors[providerId];
        return {
          editingProviderId: null,
          pendingChanges: state.pendingChanges.filter((id) => id !== providerId),
          editFormState: newFormState,
          providerErrors: newErrors,
        };
      }),

      setProviderError: (providerId, error) => set((state) => ({
        providerErrors: {
          ...state.providerErrors,
          [providerId]: error ?? undefined,
        },
      })),

      clearProviderError: (providerId) => set((state) => {
        const newErrors = { ...state.providerErrors };
        delete newErrors[providerId];
        return { providerErrors: newErrors };
      }),

      // ============ Test ============
      openTestPanel: (id, runConfig) => set({
        testPanel: {
          selectedProviderId: id,
          status: 'idle',
          cancelled: false,
          runConfig,
        },
      }),

      closeTestPanel: () => set({
        testPanel: {
          selectedProviderId: null,
          status: 'idle',
          cancelled: false,
        },
      }),

      startTest: (id, runConfig) => set((state) => ({
        testPanel: {
          ...state.testPanel,
          status: 'running',
          cancelled: false,
          ...(runConfig && { runConfig }),
        },
        providerTestStatus: {
          ...state.providerTestStatus,
          [id]: 'running',
        },
      })),

      completeTest: (id, success) => set((state) => ({
        testPanel: {
          ...state.testPanel,
          status: success ? 'success' : 'failed',
        },
        providerTestStatus: {
          ...state.providerTestStatus,
          [id]: success ? 'success' : 'failed',
        },
      })),

      cancelTest: () => set((state) => ({
        testPanel: {
          ...state.testPanel,
          status: 'failed',
          cancelled: true,
        },
        providerTestStatus: {
          ...state.providerTestStatus,
          ...(state.testPanel.selectedProviderId && {
            [state.testPanel.selectedProviderId]: 'unknown',
          }),
        },
      })),

      // ============ Connectivity ============
      startConnectivityTest: (key) => set({
        connectivityRunning: true,
        connectivityRunningKey: key,
      }),

      completeConnectivityTest: (key, result) => set((state) => ({
        connectivityResults: {
          ...state.connectivityResults,
          [key]: result,
        },
        connectivityRunning: false,
        connectivityRunningKey: null,
      })),

      clearConnectivityResult: (key) => set((state) => {
        const newResults = { ...state.connectivityResults };
        delete newResults[key];
        return { connectivityResults: newResults };
      }),

      // ============ Interview ============
      openInterviewPanel: () => set((state) => ({
        interviewPanel: {
          ...state.interviewPanel,
          open: true,
          status: 'idle',
          error: null,
        },
      })),

      closeInterviewPanel: () => set({
        interviewPanel: {
          open: false,
          status: 'idle',
          report: null,
          error: null,
        },
      }),

      startInterview: () => set((state) => ({
        interviewRunning: true,
        interviewCancelled: false,
        interviewPanel: {
          ...state.interviewPanel,
          status: 'running',
          error: null,
          report: null,
        },
      })),

      completeInterview: (report) => set((state) => ({
        interviewRunning: false,
        interviewPanel: {
          ...state.interviewPanel,
          status: 'success',
          report,
        },
      })),

      failInterview: (error) => set((state) => ({
        interviewRunning: false,
        interviewPanel: {
          ...state.interviewPanel,
          status: 'failed',
          error,
        },
      })),

      cancelInterview: () => set((state) => ({
        interviewRunning: false,
        interviewCancelled: true,
        interviewPanel: {
          ...state.interviewPanel,
          status: 'failed',
          error: '面试已取消',
        },
      })),

      // ============ Error ============
      setError: (error) => set({ globalError: error }),

      clearError: () => set({ globalError: null }),

      // ============ Config ============
      updateUnifiedConfig: (config) => set({ unifiedConfig: config }),

      // ============ Persistence ============
      clearPersistedStatus: () => {
        if (typeof window === 'undefined') return;
        try {
          localStorage.removeItem(PROVIDER_STATUS_KEY);
          localStorage.removeItem(CONNECTIVITY_RESULTS_KEY);
        } catch {
          // ignore
        }
      },

      hydrateState: (partial) => set(partial),
    }),
    {
      name: 'llm_provider_state',
      partialize: (state) => ({
        // 只持久化首选项和状态
        providerTestStatus: state.providerTestStatus,
        connectivityResults: state.connectivityResults,
      }),
      onRehydrateStorage: () => (state) => {
        // 恢复后清理过期数据
        if (state) {
          const now = Date.now();

          // 清理过期的 provider status
          const validStatus: Record<string, ConnectivityStatus> = {};
          Object.entries(state.providerTestStatus).forEach(([id, status]) => {
            // 简单判断：如果不是 'unknown' 或 'running'，保留
            if (status === 'success' || status === 'failed') {
              validStatus[id] = status;
            }
          });
          state.providerTestStatus = validStatus;

          // 清理过期的 connectivity results
          const validResults: Record<string, ConnectivityResultStrict> = {};
          Object.entries(state.connectivityResults).forEach(([key, result]) => {
            const timestamp = new Date(result.timestamp).getTime();
            if (now - timestamp <= CONNECTIVITY_TTL_MS) {
              validResults[key] = result;
            }
          });
          state.connectivityResults = validResults;
        }
      },
    }
  )
);

// ============================================================================
// Selector Hooks
// ============================================================================

/** 选择当前角色 */
export const useSelectedRole = () => useProviderStore((state) => state.selectedRole);

/** 选择当前 Provider */
export const useSelectedProvider = () => useProviderStore((state) => state.selectedProviderId);

/** 当前 Tab */
export const useActiveTab = () => useProviderStore((state) => state.activeTab);

/** 测试面板状态 */
export const useTestPanelState = () => useProviderStore((state) => state.testPanel);

/** 面试面板状态 */
export const useInterviewPanelState = () => useProviderStore((state) => state.interviewPanel);

/** Provider 连接状态 */
export const useConnectivityStatus = (providerId: string) =>
  useProviderStore((state) => state.providerTestStatus[providerId] || 'unknown');

/** Provider 是否展开 */
export const useIsProviderExpanded = (providerId: string) =>
  useProviderStore((state) => state.expandedProviders.includes(providerId));

/** 当前编辑的 Provider ID */
export const useEditingProviderId = () => useProviderStore((state) => state.editingProviderId);

/** 编辑表单状态 */
export const useEditFormState = (providerId: string) =>
  useProviderStore((state) => state.editFormState[providerId]);

/** 是否有未保存更改 */
export const useHasPendingChanges = (providerId: string) =>
  useProviderStore((state) => state.pendingChanges.includes(providerId));

/** 是否正在保存 */
export const useIsSavingProvider = (providerId: string) =>
  useProviderStore((state) => state.savingProvider === providerId);

/** Provider 错误 */
export const useProviderError = (providerId: string) =>
  useProviderStore((state) => state.providerErrors[providerId]);

/** 全局未保存更改数量 */
export const useGlobalPendingChangesCount = () =>
  useProviderStore((state) => state.pendingChanges.length);
