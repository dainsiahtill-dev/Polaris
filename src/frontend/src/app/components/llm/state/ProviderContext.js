import { jsx as _jsx } from "react/jsx-runtime";
/**
 * Provider Context
 * 提供统一的状态管理和 actions
 */
import { createContext, useContext, useReducer, useCallback, useMemo, useEffect } from 'react';
import { providerReducer, initialProviderState, ProviderActions } from './providerReducer';
// ============================================================================
// Persistence Constants
// ============================================================================
const STORAGE_KEYS = {
    PROVIDER_TEST_STATUS: 'llm_provider_test_status',
    CONNECTIVITY_RESULTS: 'llm_connectivity_results',
};
const PROVIDER_STATUS_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
const CONNECTIVITY_TTL_MS = 5 * 60 * 1000; // 5 minutes
// ============================================================================
// Persistence Utilities
// ============================================================================
function restoreProviderTestStatus() {
    if (typeof window === 'undefined')
        return {};
    try {
        const stored = localStorage.getItem(STORAGE_KEYS.PROVIDER_TEST_STATUS);
        if (!stored)
            return {};
        const parsed = JSON.parse(stored);
        const now = Date.now();
        const restored = {};
        Object.entries(parsed).forEach(([providerId, data]) => {
            if (now - data.timestamp <= PROVIDER_STATUS_TTL_MS) {
                restored[providerId] = data.status;
            }
        });
        return restored;
    }
    catch {
        return {};
    }
}
function restoreConnectivityResults() {
    if (typeof window === 'undefined')
        return new Map();
    try {
        const stored = localStorage.getItem(STORAGE_KEYS.CONNECTIVITY_RESULTS);
        if (!stored)
            return new Map();
        const parsed = JSON.parse(stored);
        const now = Date.now();
        const restored = new Map();
        Object.entries(parsed).forEach(([key, result]) => {
            const timestamp = new Date(result.timestamp).getTime();
            if (now - timestamp <= CONNECTIVITY_TTL_MS) {
                restored.set(key, result);
            }
        });
        return restored;
    }
    catch {
        return new Map();
    }
}
function persistProviderTestStatus(status) {
    if (typeof window === 'undefined')
        return;
    try {
        const toPersist = {};
        Object.entries(status).forEach(([providerId, status]) => {
            if (status === 'success' || status === 'failed') {
                toPersist[providerId] = {
                    status,
                    timestamp: Date.now(),
                };
            }
        });
        localStorage.setItem(STORAGE_KEYS.PROVIDER_TEST_STATUS, JSON.stringify(toPersist));
    }
    catch {
        // ignore storage errors
    }
}
function persistConnectivityResults(results) {
    if (typeof window === 'undefined')
        return;
    try {
        const toPersist = {};
        results.forEach((value, key) => {
            toPersist[key] = value;
        });
        localStorage.setItem(STORAGE_KEYS.CONNECTIVITY_RESULTS, JSON.stringify(toPersist));
    }
    catch {
        // ignore storage errors
    }
}
// ============================================================================
// Context Creation
// ============================================================================
const ProviderStateContext = createContext(null);
const ProviderActionsContext = createContext(null);
export function ProviderContextProvider({ children, initialState }) {
    const restoredStatus = useMemo(() => restoreProviderTestStatus(), []);
    const restoredConnectivity = useMemo(() => restoreConnectivityResults(), []);
    const [state, dispatch] = useReducer(providerReducer, {
        ...initialProviderState,
        ...initialState,
        providerTestStatus: restoredStatus,
        connectivityResults: restoredConnectivity,
    });
    useEffect(() => {
        persistProviderTestStatus(state.providerTestStatus);
        persistConnectivityResults(state.connectivityResults);
    }, [state.providerTestStatus, state.connectivityResults]);
    // ==========================================================================
    // Selection Actions
    // ==========================================================================
    const selectRole = useCallback((role) => {
        dispatch(ProviderActions.selectRole(role));
    }, []);
    const selectProvider = useCallback((id) => {
        dispatch(ProviderActions.selectProvider(id));
    }, []);
    const selectMethod = useCallback((method) => {
        dispatch(ProviderActions.selectMethod(method));
    }, []);
    // ==========================================================================
    // View Actions
    // ==========================================================================
    const switchTab = useCallback((tab) => {
        dispatch(ProviderActions.switchTab(tab));
    }, []);
    const setConfigView = useCallback((view) => {
        dispatch(ProviderActions.setConfigView(view));
    }, []);
    const setDeepView = useCallback((view) => {
        dispatch(ProviderActions.setDeepView(view));
    }, []);
    const setInterviewMode = useCallback((mode) => {
        dispatch(ProviderActions.setInterviewMode(mode));
    }, []);
    // ==========================================================================
    // Provider Card Expansion Actions
    // ==========================================================================
    const toggleExpandProvider = useCallback((id) => {
        dispatch(ProviderActions.toggleExpandProvider(id));
    }, []);
    const collapseAllProviders = useCallback(() => {
        dispatch(ProviderActions.collapseAllProviders());
    }, []);
    // ==========================================================================
    // 新的统一编辑状态 Actions
    // ==========================================================================
    const startEdit = useCallback((providerId, initialConfig) => {
        dispatch(ProviderActions.startEdit(providerId, initialConfig));
    }, []);
    const updateEditForm = useCallback((providerId, updates) => {
        dispatch(ProviderActions.updateEditForm(providerId, updates));
    }, []);
    const saveEditStart = useCallback((providerId) => {
        dispatch(ProviderActions.saveEditStart(providerId));
    }, []);
    const saveEditSuccess = useCallback((providerId) => {
        dispatch(ProviderActions.saveEditSuccess(providerId));
    }, []);
    const saveEditFailure = useCallback((providerId, error) => {
        dispatch(ProviderActions.saveEditFailure(providerId, error));
    }, []);
    const cancelEdit = useCallback((providerId) => {
        dispatch(ProviderActions.cancelEdit(providerId));
    }, []);
    const setProviderError = useCallback((providerId, error) => {
        dispatch(ProviderActions.setProviderError(providerId, error));
    }, []);
    const clearProviderError = useCallback((providerId) => {
        dispatch(ProviderActions.clearProviderError(providerId));
    }, []);
    // ==========================================================================
    // Test Actions
    // ==========================================================================
    const openTestPanel = useCallback((id, runConfig) => {
        dispatch(ProviderActions.openTestPanel(id, runConfig));
    }, []);
    const closeTestPanel = useCallback(() => {
        dispatch(ProviderActions.closeTestPanel());
    }, []);
    const startTest = useCallback((id, runConfig) => {
        dispatch(ProviderActions.startTest(id, runConfig));
    }, []);
    const completeTest = useCallback((id, success) => {
        dispatch(ProviderActions.completeTest(id, success));
    }, []);
    const cancelTest = useCallback(() => {
        dispatch(ProviderActions.cancelTest());
    }, []);
    // ==========================================================================
    // Connectivity Actions
    // ==========================================================================
    const startConnectivityTest = useCallback((key) => {
        dispatch(ProviderActions.startConnectivityTest(key));
    }, []);
    const completeConnectivityTest = useCallback((key, result) => {
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
    const completeInterview = useCallback((report) => {
        dispatch(ProviderActions.completeInterview(report));
    }, []);
    const failInterview = useCallback((error) => {
        dispatch(ProviderActions.failInterview(error));
    }, []);
    const cancelInterview = useCallback(() => {
        dispatch(ProviderActions.cancelInterview());
    }, []);
    // ==========================================================================
    // Error Actions
    // ==========================================================================
    const setError = useCallback((error) => {
        dispatch(ProviderActions.setError(error));
    }, []);
    const clearError = useCallback(() => {
        dispatch(ProviderActions.clearError());
    }, []);
    // ==========================================================================
    // Unified Config Actions
    // ==========================================================================
    const updateUnifiedConfig = useCallback((config) => {
        dispatch(ProviderActions.updateUnifiedConfig(config));
    }, []);
    // ==========================================================================
    // Persistence Actions
    // ==========================================================================
    const clearPersistedStatus = useCallback(() => {
        if (typeof window === 'undefined')
            return;
        try {
            localStorage.removeItem(STORAGE_KEYS.PROVIDER_TEST_STATUS);
            localStorage.removeItem(STORAGE_KEYS.CONNECTIVITY_RESULTS);
        }
        catch {
            // ignore
        }
    }, []);
    // ==========================================================================
    // Memoized Split Context Values
    // ==========================================================================
    // State context - only changes when state changes
    const stateValue = useMemo(() => ({ state }), [state]);
    // Actions context - only changes when dispatch changes (stable)
    const actionsValue = useMemo(() => ({
        selectRole,
        selectProvider,
        selectMethod,
        switchTab,
        setConfigView,
        setDeepView,
        setInterviewMode,
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
    }), [
        dispatch,
        selectRole,
        selectProvider,
        selectMethod,
        switchTab,
        setConfigView,
        setDeepView,
        setInterviewMode,
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
    ]);
    return (_jsx(ProviderStateContext.Provider, { value: stateValue, children: _jsx(ProviderActionsContext.Provider, { value: actionsValue, children: children }) }));
}
// ============================================================================
// Hooks
// ============================================================================
/** Hook for accessing state only - minimizes re-renders when actions are called */
export function useProviderState() {
    const context = useContext(ProviderStateContext);
    if (!context) {
        throw new Error('useProviderState must be used within ProviderContextProvider');
    }
    return context;
}
/** Hook for accessing actions only - stable reference */
export function useProviderActions() {
    const context = useContext(ProviderActionsContext);
    if (!context) {
        throw new Error('useProviderActions must be used within ProviderContextProvider');
    }
    return context;
}
// ============================================================================
// Selectors (for performance) - use split contexts internally
// ============================================================================
export function useSelectedRole() {
    const { state } = useProviderState();
    return state.selectedRole;
}
export function useSelectedProvider() {
    const { state } = useProviderState();
    return state.selectedProviderId;
}
export function useSelectedMethod() {
    const { state } = useProviderState();
    return state.selectedMethod;
}
export function useActiveTab() {
    const { state } = useProviderState();
    return state.activeTab;
}
export function useConfigView() {
    const { state } = useProviderState();
    return state.configView;
}
export function useTestPanelState() {
    const { state } = useProviderState();
    return state.testPanel;
}
export function useInterviewPanelState() {
    const { state } = useProviderState();
    return state.interviewPanel;
}
export function useConnectivityStatus(providerId) {
    const { state } = useProviderState();
    return state.providerTestStatus[providerId] || 'unknown';
}
export function useIsProviderExpanded(providerId) {
    const { state } = useProviderState();
    return state.expandedProviders.has(providerId);
}
// ============================================================================
// 新的统一编辑状态 Selectors
// ============================================================================
export function useEditingProviderId() {
    const { state } = useProviderState();
    return state.editingProviderId;
}
export function useEditFormState(providerId) {
    const { state } = useProviderState();
    return state.editFormState[providerId];
}
export function useHasPendingChanges(providerId) {
    const { state } = useProviderState();
    return state.pendingChanges.has(providerId);
}
export function useIsSavingProvider(providerId) {
    const { state } = useProviderState();
    return state.savingProvider === providerId;
}
export function useProviderError(providerId) {
    const { state } = useProviderState();
    return state.providerErrors[providerId];
}
export function useGlobalPendingChangesCount() {
    const { state } = useProviderState();
    return state.pendingChanges.size;
}
export { ProviderActions };
