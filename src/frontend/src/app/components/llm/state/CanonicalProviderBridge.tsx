/**
 * Canonical Provider Bridge
 * Phase 3: 桥接现有 ProviderContext 与新的 UnifiedLlmDataManagerV2
 * 
 * 目标:
 * - 保持 ProviderContext API 不变 (向后兼容)
 * - 内部使用 UnifiedLlmDataManagerV2 作为单一数据源
 * - 提供渐进式迁移路径
 */

import React, { 
  createContext, 
  useContext, 
  useCallback, 
  useMemo, 
  useRef,
  useEffect,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { devLogger } from '@/app/utils/devLogger';

// Canonical State Imports
import type { 
  LlmSettingsState, 
  ProviderEntity,
  UIState,
  AsyncOperationState,
} from './canonicalState';
import { createInitialState, canonicalSelectors } from './canonicalState';

// Unified Data Manager
import {
  UnifiedLlmDataManagerV2,
  ListViewAdapter,
  VisualGraphViewAdapter,
  getDefaultManager,
  type ListViewData,
  type VisualGraphViewData,
} from './UnifiedLlmDataManagerV2';

// Legacy Types (for compatibility)
import type { 
  ProviderState, 
  ProviderAction,
  ConnectivityResultStrict,
  TestStatus,
  ConnectivityStatus,
} from './providerReducer';
import type { RoleIdStrict, InterviewSuiteReportStrict } from '../types/strict';
import type { ProviderConfig, UnifiedLlmConfig, ProviderKind } from '../types';

// ============================================================================
// Bridge Context Type
// ============================================================================

interface CanonicalBridgeContextValue {
  // Legacy-compatible actions (now delegate to manager)
  state: ProviderState;
  
  // Selection
  selectRole: (role: RoleIdStrict) => void;
  selectProvider: (id: string | null) => void;
  selectMethod: (method: 'sdk' | 'api' | 'cli') => void;
  
  // View
  switchTab: (tab: 'config' | 'deepTest') => void;
  setConfigView: (view: 'list' | 'visual') => void;
  setDeepView: (view: 'hall' | 'session') => void;
  setInterviewMode: (mode: 'interactive' | 'auto') => void;
  
  // Provider Edit (Legacy-compatible)
  startEditProvider: (id: string) => void;
  stopEditProvider: () => void;
  toggleExpandProvider: (id: string) => void;
  collapseAllProviders: () => void;
  
  // New Edit Actions
  startEdit: (providerId: string, initialConfig: ProviderConfig) => void;
  updateEditForm: (providerId: string, updates: Partial<ProviderConfig>) => void;
  saveEditStart: (providerId: string) => void;
  saveEditSuccess: (providerId: string) => void;
  saveEditFailure: (providerId: string, error: string) => void;
  cancelEdit: (providerId: string) => void;
  setProviderError: (providerId: string, error: string | null | undefined) => void;
  clearProviderError: (providerId: string) => void;
  
  // Test
  openTestPanel: (id: string) => void;
  closeTestPanel: () => void;
  startTest: (id: string) => void;
  completeTest: (id: string, success: boolean) => void;
  cancelTest: () => void;
  
  // Connectivity
  startConnectivityTest: (key: string) => void;
  completeConnectivityTest: (key: string, result: ConnectivityResultStrict) => void;
  
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

  // Unified Config
  updateUnifiedConfig: (config: UnifiedLlmConfig) => void;
  
  // Direct dispatch (legacy fallback)
  dispatch: React.Dispatch<ProviderAction>;
  
  // === NEW: Canonical State Access ===
  canonicalState: LlmSettingsState;
  manager: UnifiedLlmDataManagerV2;
}

// ============================================================================
// Context Creation
// ============================================================================

const CanonicalBridgeContext = createContext<CanonicalBridgeContextValue | null>(null);

export function extractProviderIdFromConnectivityKey(key: string): string {
  if (!key) return '';
  const separator = '::';
  const separatorIndex = key.indexOf(separator);
  if (separatorIndex < 0) return key;
  return key.slice(separatorIndex + separator.length);
}

// ============================================================================
// State Mapper: Canonical → Legacy
// ============================================================================

/**
 * Map canonical state to legacy ProviderState
 * This ensures backward compatibility
 */
function mapCanonicalToLegacyState(
  canonical: LlmSettingsState,
  legacyState: ProviderState | null
): ProviderState {
  const providers = canonicalSelectors.getAllProviders(canonical);
  const roleAssignments = canonicalSelectors.getAllRoleAssignments(canonical);
  
  // Build provider test status map from canonical state
  // Priority: 1. Connectivity results, 2. Provider entity status, 3. 'unknown'
  const providerTestStatus: Record<string, ConnectivityStatus> = {};
  providers.forEach(p => {
    // Check connectivity results first (more specific, includes role)
    const connResult = canonicalSelectors.getConnectivityResultForProvider(canonical, p.id);
    if (connResult) {
      providerTestStatus[p.id] = connResult.ok ? 'success' : 'failed';
    } else {
      // Fall back to provider entity status
      providerTestStatus[p.id] = (p.status as ConnectivityStatus) || 'unknown';
    }
  });
  
  // Build expanded providers set from UI state
  const expandedProviders = new Set(canonical.ui.expandedProviderIds);
  
  return {
    // Selection (persisted in legacy, now from canonical UI state)
    selectedRole: (canonical.ui.selectedRoleId || 'pm') as RoleIdStrict,
    selectedProviderId: canonical.ui.selectedProviderId || null,
    selectedMethod: 'sdk',
    
    // View state
    activeTab: 'config',
    configView: canonical.ui.viewMode === 'visual' ? 'visual' : 'list',
    deepView: 'hall',
    interviewMode: 'interactive',
    
    // Expanded providers
    expandedProviders,
    
    // Test & Interview panels
    testPanel: {
      selectedProviderId: canonical.asyncOps.testingProviderId || null,
      status: canonical.asyncOps.testingProviderId ? 'running' : 'idle',
      cancelled: false,
    },
    interviewPanel: {
      open: !!canonical.asyncOps.interviewingRoleId,
      status: canonical.asyncOps.interviewingRoleId ? 'running' : 'idle',
      error: canonical.ui.lastError || null,
      report: null,
    },
    
    // Status maps
    providerTestStatus,
    connectivityResults: (() => {
      // Convert canonical connectivity results to Map format for legacy compatibility
      const map = new Map<string, ConnectivityResultStrict>();
      Object.entries(canonical.connectivity.results).forEach(([key, result]) => {
        map.set(key, result as ConnectivityResultStrict);
      });
      return map;
    })(),
    connectivityRunning: !!canonical.asyncOps.testingProviderId,
    connectivityRunningKey: canonical.asyncOps.testingProviderId || null,
    
    // Unified config (converted from entities) - will be refactored in Phase 5
    unifiedConfig: {} as UnifiedLlmConfig,
    
    // Legacy edit state
    editingProvider: canonical.asyncOps.savingProviderId || null,
    
    // New edit state (from canonical async ops)
    editingProviderId: canonical.asyncOps.savingProviderId || null,
    editFormState: legacyState?.editFormState || {},
    pendingChanges: new Set(),
    savingProvider: canonical.asyncOps.savingProviderId || null,
    providerErrors: {},
    
    interviewRunning: !!canonical.asyncOps.interviewingRoleId,
    interviewCancelled: false,
    
    // Errors
    globalError: canonical.ui.lastError || null,
  };
}

// ============================================================================
// Bridge Provider Component
// ============================================================================

interface CanonicalBridgeProviderProps {
  children: ReactNode;
  manager?: UnifiedLlmDataManagerV2;
  initialState?: Partial<ProviderState>;
}

export function CanonicalBridgeProvider({
  children,
  manager: externalManager,
  initialState,
}: CanonicalBridgeProviderProps) {
  // Use provided manager or default
  const managerRef = useRef(externalManager || getDefaultManager());
  const manager = managerRef.current;
  
  // Subscribe to canonical state changes
  const [canonicalState, setCanonicalState] = useState(() => manager.getState());
  
  useEffect(() => {
    return manager.subscribe((newState) => {
      setCanonicalState(newState);
    });
  }, [manager]);
  
  // Memoized legacy state mapping
  const legacyState = useMemo(() => 
    mapCanonicalToLegacyState(canonicalState, null),
    [canonicalState]
  );
  
  // ==========================================================================
  // Actions - Delegate to Manager (Single Write Path)
  // ==========================================================================
  
  // Selection Actions
  const selectRole = useCallback((role: RoleIdStrict) => {
    manager.updateUI({ selectedRoleId: role });
  }, [manager]);
  
  const selectProvider = useCallback((id: string | null) => {
    manager.updateUI({ selectedProviderId: id || undefined });
  }, [manager]);
  
  const selectMethod = useCallback((method: 'sdk' | 'api' | 'cli') => {
    // Method selection stored in UI state if needed
    // Method selection stored in UI state if needed
  }, []);
  
  // View Actions
  const switchTab = useCallback((tab: 'config' | 'deepTest') => {
    // Tab switching handled by component state
    // Tab switching handled by component state
  }, []);
  
  const setConfigView = useCallback((view: 'list' | 'visual') => {
    manager.updateUI({ viewMode: view });
  }, [manager]);
  
  const setDeepView = useCallback((view: 'hall' | 'session') => {

  }, []);
  
  const setInterviewMode = useCallback((mode: 'interactive' | 'auto') => {

  }, []);
  
  // Provider Edit Actions (Legacy)
  const startEditProvider = useCallback((id: string) => {
    manager.updateAsyncOps({ savingProviderId: id });
  }, [manager]);
  
  const stopEditProvider = useCallback(() => {
    manager.updateAsyncOps({ savingProviderId: undefined });
  }, [manager]);
  
  const toggleExpandProvider = useCallback((id: string) => {
    const currentExpanded = new Set(canonicalState.ui.expandedProviderIds);
    if (currentExpanded.has(id)) {
      currentExpanded.delete(id);
    } else {
      currentExpanded.add(id);
    }
    manager.updateUI({ expandedProviderIds: Array.from(currentExpanded) });
  }, [manager, canonicalState.ui.expandedProviderIds]);
  
  const collapseAllProviders = useCallback(() => {
    manager.updateUI({ expandedProviderIds: [] });
  }, [manager]);
  
  // New Edit Actions
  const startEdit = useCallback((providerId: string, initialConfig: ProviderConfig) => {
    manager.updateAsyncOps({ savingProviderId: providerId });
    // Store form state in legacy format for compatibility
    // Store form state in legacy format for compatibility
  }, [manager]);
  
  const updateEditForm = useCallback((providerId: string, updates: Partial<ProviderConfig>) => {
    // Form updates handled locally in component during edit
    // Form updates handled locally in component during edit
  }, []);
  
  const saveEditStart = useCallback((providerId: string) => {
    manager.updateAsyncOps({ savingProviderId: providerId });
  }, [manager]);
  
  const saveEditSuccess = useCallback((providerId: string) => {
    manager.updateAsyncOps({ savingProviderId: undefined });
  }, [manager]);
  
  const saveEditFailure = useCallback((providerId: string, error: string) => {
    manager.updateUI({ lastError: error });
    manager.updateAsyncOps({ savingProviderId: undefined });
  }, [manager]);
  
  const cancelEdit = useCallback((providerId: string) => {
    manager.updateAsyncOps({ savingProviderId: undefined });
  }, [manager]);
  
  const setProviderError = useCallback((providerId: string, error: string | null | undefined) => {
    if (error) {
      manager.updateProvider(providerId, { lastError: error });
    }
  }, [manager]);
  
  const clearProviderError = useCallback((providerId: string) => {
    manager.updateProvider(providerId, { lastError: undefined });
  }, [manager]);
  
  // Test Actions
  const openTestPanel = useCallback((id: string) => {
    manager.updateAsyncOps({ testingProviderId: id });
  }, [manager]);
  
  const closeTestPanel = useCallback(() => {
    manager.updateAsyncOps({ testingProviderId: undefined });
  }, [manager]);
  
  const startTest = useCallback((id: string) => {
    manager.updateAsyncOps({ testingProviderId: id });
    manager.updateProvider(id, { status: 'testing' });
  }, [manager]);
  
  const completeTest = useCallback((id: string, success: boolean) => {
    manager.updateAsyncOps({ testingProviderId: undefined });
    manager.updateProvider(id, { 
      status: success ? 'ready' : 'failed',
      lastTest: {
        at: new Date().toISOString(),
      }
    });
  }, [manager]);
  
  const cancelTest = useCallback(() => {
    manager.updateAsyncOps({ testingProviderId: undefined });
  }, [manager]);
  
  // Connectivity Actions
  const startConnectivityTest = useCallback((key: string) => {
    const providerId = extractProviderIdFromConnectivityKey(key);
    if (providerId) {
      manager.updateProvider(providerId, { status: 'testing' });
    }
  }, [manager]);
  
  const completeConnectivityTest = useCallback((key: string, result: ConnectivityResultStrict) => {
    const providerId = extractProviderIdFromConnectivityKey(key);
    if (providerId) {
      // Update provider entity status
      manager.updateProvider(providerId, { 
        status: result.ok ? 'ready' : 'failed',
        lastError: result.error,
        lastTest: {
          at: new Date().toISOString(),
          latencyMs: result.latencyMs,
        }
      });
      // Also save to canonical connectivity state for persistence
      manager.updateConnectivityResult(key, {
        ok: result.ok,
        timestamp: result.timestamp,
        latencyMs: result.latencyMs,
        error: result.error,
        model: result.model,
        sourceRole: result.sourceRole,
        thinking: result.thinking,
      });
    }
  }, [manager]);
  
  // Interview Actions
  const openInterviewPanel = useCallback(() => {

  }, []);
  
  const closeInterviewPanel = useCallback(() => {
    manager.updateAsyncOps({ interviewingRoleId: undefined });
  }, [manager]);
  
  const startInterview = useCallback(() => {

  }, []);
  
  const completeInterview = useCallback((report: InterviewSuiteReportStrict) => {
    manager.updateAsyncOps({ interviewingRoleId: undefined });

  }, [manager]);
  
  const failInterview = useCallback((error: string) => {
    manager.updateUI({ lastError: error });
    manager.updateAsyncOps({ interviewingRoleId: undefined });
  }, [manager]);
  
  const cancelInterview = useCallback(() => {
    manager.updateAsyncOps({ interviewingRoleId: undefined });
  }, [manager]);
  
  // Error Actions
  const setError = useCallback((error: string | null) => {
    manager.updateUI({ lastError: error || undefined });
  }, [manager]);
  
  const clearError = useCallback(() => {
    manager.updateUI({ lastError: undefined });
  }, [manager]);
  
  // Unified Config
  const updateUnifiedConfig = useCallback((config: UnifiedLlmConfig) => {
    // Convert UnifiedLlmConfig to canonical entities
    Object.entries(config.providers || {}).forEach(([id, unifiedProvider]) => {
      const existing = canonicalState.entities.providers[id];
      const providerConfig = unifiedProvider.config;
      if (existing) {
        manager.updateProvider(id, {
          config: providerConfig,
          name: unifiedProvider.name || existing.name,
          type: unifiedProvider.type || existing.type,
        });
      } else {
        // Create new provider entity from UnifiedProvider
        const newEntity: ProviderEntity = {
          id,
          name: unifiedProvider.name || id,
          kind: (unifiedProvider.type as ProviderKind) || 'openai_compat',
          type: unifiedProvider.type || '',
          conn: providerConfig?.conn 
            ? { kind: 'http' as const, baseUrl: providerConfig.base_url || '', ...providerConfig.conn }
            : { kind: 'http' as const, baseUrl: providerConfig?.base_url || '' },
          modelId: providerConfig?.model || providerConfig?.default_model || '',
          status: 'untested',
          config: providerConfig || { type: unifiedProvider.type } as ProviderConfig,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        };
        manager.addProvider(newEntity);
      }
    });
  }, [manager, canonicalState.entities.providers]);
  
  // Legacy dispatch (no-op in new architecture)
  const dispatch = useCallback((action: ProviderAction) => {
    devLogger.warn('[Bridge] Legacy dispatch called:', action);
    // In migration phase, actions should use the new methods
  }, []);
  
  // ==========================================================================
  // Memoized Value
  // ==========================================================================
  const value = useMemo<CanonicalBridgeContextValue>(
    () => ({
      state: legacyState,
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
      dispatch,
      // NEW: Canonical access
      canonicalState,
      manager,
    }),
    [
      legacyState,
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
      dispatch,
      canonicalState,
      manager,
    ]
  );
  
  return (
    <CanonicalBridgeContext.Provider value={value}>
      {children}
    </CanonicalBridgeContext.Provider>
  );
}

// ============================================================================
// Hook
// ============================================================================

export function useCanonicalBridge(): CanonicalBridgeContextValue {
  const context = useContext(CanonicalBridgeContext);
  if (!context) {
    throw new Error('useCanonicalBridge must be used within CanonicalBridgeProvider');
  }
  return context;
}

// ============================================================================
// Compatibility Hooks
// ============================================================================

/**
 * Hook for accessing canonical state directly
 * Use this for new components that want to use the new architecture
 */
export function useCanonicalState() {
  const { canonicalState, manager } = useCanonicalBridge();
  return { state: canonicalState, manager };
}

/**
 * Hook for accessing view data through adapters
 */
export function useListViewData() {
  const { manager } = useCanonicalBridge();
  const [data, setData] = useState<ListViewData>(() => manager.getViewData<ListViewData>('list'));
  
  useEffect(() => {
    return manager.subscribe(() => {
      setData(manager.getViewData<ListViewData>('list'));
    });
  }, [manager]);
  
  return data;
}

/**
 * Hook for accessing visual graph view data
 */
export function useVisualGraphViewData() {
  const { manager } = useCanonicalBridge();
  const [data, setData] = useState<VisualGraphViewData>(() => manager.getViewData<VisualGraphViewData>('visual'));
  
  useEffect(() => {
    return manager.subscribe(() => {
      setData(manager.getViewData<VisualGraphViewData>('visual'));
    });
  }, [manager]);
  
  return data;
}
