/**
 * Provider Context
 * 提供统一的状态管理和 actions
 */

import React, { createContext, useContext, useReducer, useCallback, useMemo, useEffect } from 'react';
import type { ReactNode } from 'react';
import type {
  ProviderState,
  ProviderAction,
  ConnectivityResultStrict,
  TestStatus,
  ConnectivityStatus,
} from './providerReducer';
import type { RoleIdStrict, InterviewSuiteReportStrict } from '../types/strict';
import {
  providerReducer,
  initialProviderState,
  ProviderActions
} from './providerReducer';
import type { ProviderConfig, UnifiedLlmConfig } from '../types';

// ============================================================================
// Persistence Constants
// ============================================================================

const STORAGE_KEYS = {
  PROVIDER_TEST_STATUS: 'llm_provider_test_status',
  CONNECTIVITY_RESULTS: 'llm_connectivity_results',
} as const;

const PROVIDER_STATUS_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
const CONNECTIVITY_TTL_MS = 5 * 60 * 1000; // 5 minutes

interface PersistedProviderStatus {
  status: 'success' | 'failed';
  timestamp: number;
  model?: string;
}

// ============================================================================
// Persistence Utilities
// ============================================================================

function restoreProviderTestStatus(): Record<string, ConnectivityStatus> {
  if (typeof window === 'undefined') return {};

  try {
    const stored = localStorage.getItem(STORAGE_KEYS.PROVIDER_TEST_STATUS);
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

function restoreConnectivityResults(): Map<string, ConnectivityResultStrict> {
  if (typeof window === 'undefined') return new Map();

  try {
    const stored = localStorage.getItem(STORAGE_KEYS.CONNECTIVITY_RESULTS);
    if (!stored) return new Map();

    const parsed: Record<string, ConnectivityResultStrict> = JSON.parse(stored);
    const now = Date.now();
    const restored = new Map<string, ConnectivityResultStrict>();

    Object.entries(parsed).forEach(([key, result]) => {
      const timestamp = new Date(result.timestamp).getTime();
      if (now - timestamp <= CONNECTIVITY_TTL_MS) {
        restored.set(key, result);
      }
    });

    return restored;
  } catch {
    return new Map();
  }
}

function persistProviderTestStatus(status: Record<string, ConnectivityStatus>): void {
  if (typeof window === 'undefined') return;

  try {
    const toPersist: Record<string, PersistedProviderStatus> = {};
    Object.entries(status).forEach(([providerId, status]) => {
      if (status === 'success' || status === 'failed') {
        toPersist[providerId] = {
          status,
          timestamp: Date.now(),
        };
      }
    });
    localStorage.setItem(STORAGE_KEYS.PROVIDER_TEST_STATUS, JSON.stringify(toPersist));
  } catch {
    // ignore storage errors
  }
}

function persistConnectivityResults(results: Map<string, ConnectivityResultStrict>): void {
  if (typeof window === 'undefined') return;

  try {
    const toPersist: Record<string, ConnectivityResultStrict> = {};
    results.forEach((value, key) => {
      toPersist[key] = value;
    });
    localStorage.setItem(STORAGE_KEYS.CONNECTIVITY_RESULTS, JSON.stringify(toPersist));
  } catch {
    // ignore storage errors
  }
}

// ============================================================================
// Context Type
// ============================================================================

/** Split context types for optimized re-renders */
interface ProviderStateContextValue {
  state: ProviderState;
}

interface ProviderActionsContextValue {
  // Actions - Selection
  selectRole: (role: RoleIdStrict) => void;
  selectProvider: (id: string | null) => void;
  selectMethod: (method: 'sdk' | 'api' | 'cli') => void;

  // Actions - View
  switchTab: (tab: 'config' | 'deepTest') => void;
  setConfigView: (view: 'list' | 'visual') => void;
  setDeepView: (view: 'hall' | 'session') => void;
  setInterviewMode: (mode: 'interactive' | 'auto') => void;

  // Actions - Provider Edit (Legacy)
  startEditProvider: (id: string) => void;
  stopEditProvider: () => void;
  toggleExpandProvider: (id: string) => void;
  collapseAllProviders: () => void;

  // === 新的统一编辑状态 Actions ===
  startEdit: (providerId: string, initialConfig: ProviderConfig) => void;
  updateEditForm: (providerId: string, updates: Partial<ProviderConfig>) => void;
  saveEditStart: (providerId: string) => void;
  saveEditSuccess: (providerId: string) => void;
  saveEditFailure: (providerId: string, error: string) => void;
  cancelEdit: (providerId: string) => void;
  setProviderError: (providerId: string, error: string | null | undefined) => void;
  clearProviderError: (providerId: string) => void;

  // Actions - Test
  openTestPanel: (id: string, runConfig?: { suites?: string[]; role?: string; model?: string }) => void;
  closeTestPanel: () => void;
  startTest: (id: string, runConfig?: { suites?: string[]; role?: string; model?: string }) => void;
  completeTest: (id: string, success: boolean) => void;
  cancelTest: () => void;

  // Actions - Connectivity
  startConnectivityTest: (key: string) => void;
  completeConnectivityTest: (key: string, result: ConnectivityResultStrict) => void;

  // Actions - Interview
  openInterviewPanel: () => void;
  closeInterviewPanel: () => void;
  startInterview: () => void;
  completeInterview: (report: InterviewSuiteReportStrict) => void;
  failInterview: (error: string) => void;
  cancelInterview: () => void;

  // Actions - Error
  setError: (error: string | null) => void;
  clearError: () => void;

  // Actions - Unified Config
  updateUnifiedConfig: (config: UnifiedLlmConfig) => void;

  // Actions - Persistence
  clearPersistedStatus: () => void;

  // Direct dispatch (for complex cases)
  dispatch: React.Dispatch<ProviderAction>;
}

/** Legacy combined context (backward compatibility) */
interface ProviderContextValue extends ProviderStateContextValue, ProviderActionsContextValue {}

// ============================================================================
// Context Creation
// ============================================================================

const ProviderStateContext = createContext<ProviderStateContextValue | null>(null);
const ProviderActionsContext = createContext<ProviderActionsContextValue | null>(null);

/** @deprecated Use split contexts or selector hooks instead */
const ProviderContext = createContext<ProviderContextValue | null>(null);

// ============================================================================
// Provider Component
// ============================================================================

interface ProviderContextProviderProps {
  children: ReactNode;
  initialState?: Partial<ProviderState>;
}

export function ProviderContextProvider({
  children,
  initialState
}: ProviderContextProviderProps) {
  const restoredStatus = useMemo(() => restoreProviderTestStatus(), []);
  const restoredConnectivity = useMemo(() => restoreConnectivityResults(), []);

  const [state, dispatch] = useReducer(
    providerReducer,
    {
      ...initialProviderState,
      ...initialState,
      providerTestStatus: restoredStatus,
      connectivityResults: restoredConnectivity,
    }
  );

  useEffect(() => {
    persistProviderTestStatus(state.providerTestStatus);
    persistConnectivityResults(state.connectivityResults);
  }, [state.providerTestStatus, state.connectivityResults]);

  // ==========================================================================
  // Selection Actions
  // ==========================================================================
  const selectRole = useCallback((role: RoleIdStrict) => {
    dispatch(ProviderActions.selectRole(role));
  }, []);

  const selectProvider = useCallback((id: string | null) => {
    dispatch(ProviderActions.selectProvider(id));
  }, []);

  const selectMethod = useCallback((method: 'sdk' | 'api' | 'cli') => {
    dispatch(ProviderActions.selectMethod(method));
  }, []);

  // ==========================================================================
  // View Actions
  // ==========================================================================
  const switchTab = useCallback((tab: 'config' | 'deepTest') => {
    dispatch(ProviderActions.switchTab(tab));
  }, []);

  const setConfigView = useCallback((view: 'list' | 'visual') => {
    dispatch(ProviderActions.setConfigView(view));
  }, []);

  const setDeepView = useCallback((view: 'hall' | 'session') => {
    dispatch(ProviderActions.setDeepView(view));
  }, []);

  const setInterviewMode = useCallback((mode: 'interactive' | 'auto') => {
    dispatch(ProviderActions.setInterviewMode(mode));
  }, []);

  // ==========================================================================
  // Provider Edit Actions (Legacy)
  // ==========================================================================
  const startEditProvider = useCallback((id: string) => {
    dispatch(ProviderActions.startEditProvider(id));
  }, []);

  const stopEditProvider = useCallback(() => {
    dispatch(ProviderActions.stopEditProvider());
  }, []);

  const toggleExpandProvider = useCallback((id: string) => {
    dispatch(ProviderActions.toggleExpandProvider(id));
  }, []);

  const collapseAllProviders = useCallback(() => {
    dispatch(ProviderActions.collapseAllProviders());
  }, []);

  // ==========================================================================
  // 新的统一编辑状态 Actions
  // ==========================================================================
  const startEdit = useCallback((providerId: string, initialConfig: ProviderConfig) => {
    dispatch(ProviderActions.startEdit(providerId, initialConfig));
  }, []);

  const updateEditForm = useCallback((providerId: string, updates: Partial<ProviderConfig>) => {
    dispatch(ProviderActions.updateEditForm(providerId, updates));
  }, []);

  const saveEditStart = useCallback((providerId: string) => {
    dispatch(ProviderActions.saveEditStart(providerId));
  }, []);

  const saveEditSuccess = useCallback((providerId: string) => {
    dispatch(ProviderActions.saveEditSuccess(providerId));
  }, []);

  const saveEditFailure = useCallback((providerId: string, error: string) => {
    dispatch(ProviderActions.saveEditFailure(providerId, error));
  }, []);

  const cancelEdit = useCallback((providerId: string) => {
    dispatch(ProviderActions.cancelEdit(providerId));
  }, []);

  const setProviderError = useCallback((providerId: string, error: string | null | undefined) => {
    dispatch(ProviderActions.setProviderError(providerId, error));
  }, []);

  const clearProviderError = useCallback((providerId: string) => {
    dispatch(ProviderActions.clearProviderError(providerId));
  }, []);

  // ==========================================================================
  // Test Actions
  // ==========================================================================
  const openTestPanel = useCallback((id: string, runConfig?: { suites?: string[]; role?: string; model?: string }) => {
    dispatch(ProviderActions.openTestPanel(id, runConfig));
  }, []);

  const closeTestPanel = useCallback(() => {
    dispatch(ProviderActions.closeTestPanel());
  }, []);

  const startTest = useCallback((id: string, runConfig?: { suites?: string[]; role?: string; model?: string }) => {
    dispatch(ProviderActions.startTest(id, runConfig));
  }, []);

  const completeTest = useCallback((id: string, success: boolean) => {
    dispatch(ProviderActions.completeTest(id, success));
  }, []);

  const cancelTest = useCallback(() => {
    dispatch(ProviderActions.cancelTest());
  }, []);

  // ==========================================================================
  // Connectivity Actions
  // ==========================================================================
  const startConnectivityTest = useCallback((key: string) => {
    dispatch(ProviderActions.startConnectivityTest(key));
  }, []);

  const completeConnectivityTest = useCallback((key: string, result: ConnectivityResultStrict) => {
    dispatch(ProviderActions.completeConnectivityTest(key, result));
  }, []);

  // ==========================================================================
  // Interview Actions
  // ==========================================================================
  const openInterviewPanel = useCallback(() => {
    dispatch(ProviderActions.openInterviewPanel());
  }, []);

  const closeInterviewPanel = useCallback(() => {
    dispatch(ProviderActions.closeInterviewPanel());
  }, []);

  const startInterview = useCallback(() => {
    dispatch(ProviderActions.startInterview());
  }, []);

  const completeInterview = useCallback((report: InterviewSuiteReportStrict) => {
    dispatch(ProviderActions.completeInterview(report));
  }, []);

  const failInterview = useCallback((error: string) => {
    dispatch(ProviderActions.failInterview(error));
  }, []);

  const cancelInterview = useCallback(() => {
    dispatch(ProviderActions.cancelInterview());
  }, []);

  // ==========================================================================
  // Error Actions
  // ==========================================================================
  const setError = useCallback((error: string | null) => {
    dispatch(ProviderActions.setError(error));
  }, []);

  const clearError = useCallback(() => {
    dispatch(ProviderActions.clearError());
  }, []);

  // ==========================================================================
  // Unified Config Actions
  // ==========================================================================
  const updateUnifiedConfig = useCallback((config: UnifiedLlmConfig) => {
    dispatch(ProviderActions.updateUnifiedConfig(config));
  }, []);

  // ==========================================================================
  // Persistence Actions
  // ==========================================================================
  const clearPersistedStatus = useCallback(() => {
    if (typeof window === 'undefined') return;
    try {
      localStorage.removeItem(STORAGE_KEYS.PROVIDER_TEST_STATUS);
      localStorage.removeItem(STORAGE_KEYS.CONNECTIVITY_RESULTS);
    } catch {
      // ignore
    }
  }, []);

  // ==========================================================================
  // Memoized Split Context Values
  // ==========================================================================

  // State context - only changes when state changes
  const stateValue = useMemo<ProviderStateContextValue>(
    () => ({ state }),
    [state]
  );

  // Actions context - only changes when dispatch changes (stable)
  const actionsValue = useMemo<ProviderActionsContextValue>(
    () => ({
      selectRole,
      selectProvider,
      selectMethod,
      switchTab,
      setConfigView,
      setDeepView,
      setInterviewMode,
      startEditProvider,
      stopEditProvider,
      toggleExpandProvider,
      collapseAllProviders,
      startEdit,
      updateEditForm,
      saveEditStart,
      saveEditSuccess,
      saveEditFailure,
      cancelEdit,
      setProviderError,
      clearProviderError,
      openTestPanel,
      closeTestPanel,
      startTest,
      completeTest,
      cancelTest,
      startConnectivityTest,
      completeConnectivityTest,
      openInterviewPanel,
      closeInterviewPanel,
      startInterview,
      completeInterview,
      failInterview,
      cancelInterview,
      setError,
      clearError,
      updateUnifiedConfig,
      clearPersistedStatus,
      dispatch,
    }),
    [
      dispatch,
      selectRole,
      selectProvider,
      selectMethod,
      switchTab,
      setConfigView,
      setDeepView,
      setInterviewMode,
      startEditProvider,
      stopEditProvider,
      toggleExpandProvider,
      collapseAllProviders,
      startEdit,
      updateEditForm,
      saveEditStart,
      saveEditSuccess,
      saveEditFailure,
      cancelEdit,
      setProviderError,
      clearProviderError,
      openTestPanel,
      closeTestPanel,
      startTest,
      completeTest,
      cancelTest,
      startConnectivityTest,
      completeConnectivityTest,
      openInterviewPanel,
      closeInterviewPanel,
      startInterview,
      completeInterview,
      failInterview,
      cancelInterview,
      setError,
      clearError,
      updateUnifiedConfig,
      clearPersistedStatus,
    ]
  );

  // Legacy combined value (backward compatibility)
  const legacyValue = useMemo<ProviderContextValue>(
    () => ({
      ...stateValue,
      ...actionsValue,
    }),
    [stateValue, actionsValue]
  );

  return (
    <ProviderStateContext.Provider value={stateValue}>
      <ProviderActionsContext.Provider value={actionsValue}>
        <ProviderContext.Provider value={legacyValue}>
          {children}
        </ProviderContext.Provider>
      </ProviderActionsContext.Provider>
    </ProviderStateContext.Provider>
  );
}

// ============================================================================
// Hooks
// ============================================================================

/** Hook for accessing state only - minimizes re-renders when actions are called */
export function useProviderState(): ProviderStateContextValue {
  const context = useContext(ProviderStateContext);
  if (!context) {
    throw new Error('useProviderState must be used within ProviderContextProvider');
  }
  return context;
}

/** Hook for accessing actions only - stable reference */
export function useProviderActions(): ProviderActionsContextValue {
  const context = useContext(ProviderActionsContext);
  if (!context) {
    throw new Error('useProviderActions must be used within ProviderContextProvider');
  }
  return context;
}

/** @deprecated Use useProviderState + useProviderActions or selector hooks instead */
export function useProviderContext(): ProviderContextValue {
  const context = useContext(ProviderContext);
  if (!context) {
    throw new Error('useProviderContext must be used within ProviderContextProvider');
  }
  return context;
}

// ============================================================================
// Selectors (for performance) - use split contexts internally
// ============================================================================

export function useSelectedRole(): RoleIdStrict {
  const { state } = useProviderState();
  return state.selectedRole;
}

export function useSelectedProvider(): string | null {
  const { state } = useProviderState();
  return state.selectedProviderId;
}

export function useActiveTab(): 'config' | 'deepTest' {
  const { state } = useProviderState();
  return state.activeTab;
}

export function useTestPanelState(): { selectedProviderId: string | null; status: TestStatus } {
  const { state } = useProviderState();
  return state.testPanel;
}

export function useInterviewPanelState(): {
  open: boolean;
  status: TestStatus;
  error: string | null;
  report: InterviewSuiteReportStrict | null;
} {
  const { state } = useProviderState();
  return state.interviewPanel;
}

export function useConnectivityStatus(providerId: string): ConnectivityStatus {
  const { state } = useProviderState();
  return state.providerTestStatus[providerId] || 'unknown';
}

export function useIsProviderExpanded(providerId: string): boolean {
  const { state } = useProviderState();
  return state.expandedProviders.has(providerId);
}

// ============================================================================
// 新的统一编辑状态 Selectors
// ============================================================================

export function useEditingProviderId(): string | null {
  const { state } = useProviderState();
  return state.editingProviderId;
}

export function useEditFormState(providerId: string): ProviderConfig | undefined {
  const { state } = useProviderState();
  return state.editFormState[providerId];
}

export function useHasPendingChanges(providerId: string): boolean {
  const { state } = useProviderState();
  return state.pendingChanges.has(providerId);
}

export function useIsSavingProvider(providerId: string): boolean {
  const { state } = useProviderState();
  return state.savingProvider === providerId;
}

export function useProviderError(providerId: string): string | undefined {
  const { state } = useProviderState();
  return state.providerErrors[providerId];
}

export function useGlobalPendingChangesCount(): number {
  const { state } = useProviderState();
  return state.pendingChanges.size;
}

export { ProviderActions };
